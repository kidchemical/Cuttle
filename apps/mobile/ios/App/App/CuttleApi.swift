import Foundation

enum CuttleApi {
    static func eventsURL() -> URL? {
        let base = CuttleNotifyPrefs.baseUrl.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !base.isEmpty else { return nil }
        var parts = URLComponents(string: base + "/api/mobile/events")
        parts?.queryItems = [
            URLQueryItem(name: "device_id", value: CuttleNotifyPrefs.deviceId),
            URLQueryItem(name: "token", value: CuttleNotifyPrefs.token)
        ]
        return parts?.url
    }

    static func submitReply(interactionId: String, answer: String, session: URLSession) {
        let base = CuttleNotifyPrefs.baseUrl.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: base + "/api/mobile/reply") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        let body: [String: String] = [
            "token": CuttleNotifyPrefs.token,
            "interaction_id": interactionId,
            "answer": answer
        ]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        session.dataTask(with: req).resume()
    }
}
