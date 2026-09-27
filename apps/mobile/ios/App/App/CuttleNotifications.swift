import Foundation
import UserNotifications

enum CuttleNotifications {
    static let interactionCategory = "CUTTLE_INTERACTION"
    static let actionYes = "YES"
    static let actionNo = "NO"

    static func register() {
        let yes = UNNotificationAction(identifier: actionYes, title: "Yes", options: [])
        let no = UNNotificationAction(identifier: actionNo, title: "No", options: [])
        let cat = UNNotificationCategory(
            identifier: interactionCategory,
            actions: [yes, no],
            intentIdentifiers: [],
            options: []
        )
        UNUserNotificationCenter.current().setNotificationCategories([cat])
    }

    static func requestPermissionThen(_ done: @escaping (Bool) -> Void) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { ok, _ in
            DispatchQueue.main.async { done(ok) }
        }
    }

    static func showChatComplete(pipeline: String, text: String, sessionId: String = "") {
        let title = pipeline.isEmpty || pipeline == "null" ? "Cuttle" : "Cuttle · \(pipeline)"
        var body = text.isEmpty ? "Done" : text
        if body.count > 400 {
            body = String(body.prefix(400)) + "…"
        }
        var info = ["type": "chat_complete"]
        if !sessionId.isEmpty {
            info["session_id"] = sessionId
        }
        post(title: title, body: body, category: nil, userInfo: info)
    }

    static func showInteraction(interactionId: String, question: String) {
        guard !interactionId.isEmpty else { return }
        post(
            title: "Cuttle needs input",
            body: question.isEmpty ? "Question" : question,
            category: interactionCategory,
            userInfo: [
                "type": "interaction",
                "interaction_id": interactionId
            ]
        )
    }

    static func handleAction(response: UNNotificationResponse) {
        let info = response.notification.request.content.userInfo
        guard let interactionId = info["interaction_id"] as? String, !interactionId.isEmpty else { return }
        let answer: String
        switch response.actionIdentifier {
        case actionYes:
            answer = "Yes"
        case actionNo:
            answer = "No"
        default:
            return
        }
        CuttleApi.submitReply(interactionId: interactionId, answer: answer, session: CuttleEventStream.shared.session)
    }

    private static func post(title: String, body: String, category: String?, userInfo: [String: String]) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        if let category = category {
            content.categoryIdentifier = category
        }
        content.userInfo = userInfo
        let req = UNNotificationRequest(
            identifier: UUID().uuidString,
            content: content,
            trigger: nil
        )
        UNUserNotificationCenter.current().add(req, withCompletionHandler: nil)
    }
}
