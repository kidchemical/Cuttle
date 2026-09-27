import Foundation

enum CuttleNotifyPrefs {
    static let prefix = "CapacitorStorage."
    static let defaultToken = "dev-local-token"

    static var baseUrl: String {
        (UserDefaults.standard.string(forKey: prefix + "cuttle_base_url") ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static var token: String {
        let raw = (UserDefaults.standard.string(forKey: prefix + "cuttle_mobile_token") ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return raw.isEmpty ? defaultToken : raw
    }

    /// Default on when the key is missing so existing installs pick this up.
    static var enabled: Bool {
        UserDefaults.standard.string(forKey: prefix + "cuttle_notify_enabled") != "0"
    }

    static var deviceId: String {
        let key = prefix + "cuttle_device_id"
        if let existing = UserDefaults.standard.string(forKey: key), !existing.isEmpty {
            return existing
        }
        let id = "ios-" + UUID().uuidString
        UserDefaults.standard.set(id, forKey: key)
        return id
    }

    static var shouldListen: Bool {
        enabled && !baseUrl.isEmpty
    }
}
