import Foundation
import UIKit

/// LAN SSE listener. Runs while the app is active; iOS may keep it briefly
/// after backgrounding. Reconnects when the app becomes active again.
final class CuttleEventStream: NSObject, URLSessionDataDelegate {
    static let shared = CuttleEventStream()
    static var appInForeground = true

    private(set) lazy var session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 120
        config.timeoutIntervalForResource = 60 * 60 * 24 * 7
        config.waitsForConnectivity = true
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()

    private var task: URLSessionDataTask?
    private var buffer = Data()
    private var backoff: TimeInterval = 1
    private var stopping = true
    private var startedURL = ""
    private let lock = NSLock()
    private var bgTask = UIBackgroundTaskIdentifier.invalid

    func apply() {
        if !CuttleNotifyPrefs.shouldListen {
            startedURL = ""
            stop()
            return
        }
        let url = CuttleApi.eventsURL()?.absoluteString ?? ""
        if !stopping && url == startedURL {
            return
        }
        startedURL = url
        start()
    }

    func start() {
        stopping = false
        CuttleNotifications.register()
        CuttleNotifications.requestPermissionThen { _ in }
        connect()
    }

    func stop() {
        stopping = true
        lock.lock()
        task?.cancel()
        task = nil
        buffer.removeAll()
        lock.unlock()
        endBackgroundTask()
    }

    func beginBackgroundGrace() {
        endBackgroundTask()
        bgTask = UIApplication.shared.beginBackgroundTask(withName: "cuttle-sse") { [weak self] in
            self?.endBackgroundTask()
        }
    }

    func endBackgroundTask() {
        if bgTask != .invalid {
            UIApplication.shared.endBackgroundTask(bgTask)
            bgTask = .invalid
        }
    }

    private func connect() {
        guard !stopping, CuttleNotifyPrefs.shouldListen, let url = CuttleApi.eventsURL() else {
            return
        }
        lock.lock()
        task?.cancel()
        buffer.removeAll()
        var req = URLRequest(url: url)
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        req.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        let next = session.dataTask(with: req)
        task = next
        lock.unlock()
        next.resume()
    }

    private func scheduleReconnect() {
        guard !stopping else { return }
        let delay = backoff
        backoff = min(30, backoff * 2)
        DispatchQueue.global().asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.connect()
        }
    }

    private func handleEventJSON(_ raw: String) {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let data = trimmed.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return
        }
        let type = obj["type"] as? String ?? ""
        if type == "chat_complete" {
            if CuttleEventStream.appInForeground { return }
            let pipeline = obj["pipeline"] as? String ?? "Cuttle"
            let text = obj["response"] as? String ?? "Done"
            let sessionId = String(describing: obj["session_id"] ?? "")
            DispatchQueue.main.async {
                CuttleNotifications.showChatComplete(
                    pipeline: pipeline,
                    text: text,
                    sessionId: sessionId == "nil" ? "" : sessionId
                )
            }
        } else if type == "interaction" {
            if CuttleEventStream.appInForeground { return }
            let iid = obj["interaction_id"] as? String ?? ""
            let question = obj["question"] as? String ?? "Question"
            DispatchQueue.main.async {
                CuttleNotifications.showInteraction(interactionId: iid, question: question)
            }
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        lock.lock()
        buffer.append(data)
        guard let text = String(data: buffer, encoding: .utf8) else {
            lock.unlock()
            return
        }
        let parts = text.components(separatedBy: "\n\n")
        if parts.count > 1 {
            let leftover = parts.last ?? ""
            buffer = leftover.data(using: .utf8) ?? Data()
            lock.unlock()
            for block in parts.dropLast() {
                for line in block.components(separatedBy: "\n") {
                    if line.hasPrefix("data:") {
                        let payload = String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces)
                        handleEventJSON(payload)
                    }
                }
            }
        } else {
            lock.unlock()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock()
        self.task = nil
        lock.unlock()
        if stopping { return }
        scheduleReconnect()
    }

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        // LAN self-signed cert — same trust decision as the WebView.
        if let trust = challenge.protectionSpace.serverTrust {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }
}
