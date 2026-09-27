import UIKit
import Capacitor
import UserNotifications

@UIApplicationMain
class AppDelegate: UIResponder, UIApplicationDelegate, UNUserNotificationCenterDelegate {

    var window: UIWindow?

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        CuttleNotifications.register()
        application.setMinimumBackgroundFetchInterval(UIApplication.backgroundFetchIntervalMinimum)
        NotificationCenter.default.addObserver(
            forName: UserDefaults.didChangeNotification,
            object: nil,
            queue: .main
        ) { _ in
            CuttleEventStream.shared.apply()
        }
        CuttleEventStream.shared.apply()
        return true
    }

    func applicationWillResignActive(_ application: UIApplication) {
        CuttleEventStream.appInForeground = false
    }

    func applicationDidEnterBackground(_ application: UIApplication) {
        CuttleEventStream.appInForeground = false
        CuttleEventStream.shared.beginBackgroundGrace()
    }

    func applicationWillEnterForeground(_ application: UIApplication) {
        CuttleEventStream.shared.endBackgroundTask()
        CuttleEventStream.shared.apply()
    }

    func applicationDidBecomeActive(_ application: UIApplication) {
        CuttleEventStream.appInForeground = true
        CuttleEventStream.shared.endBackgroundTask()
        CuttleEventStream.shared.apply()
    }

    func applicationWillTerminate(_ application: UIApplication) {
        CuttleEventStream.shared.stop()
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        if CuttleEventStream.appInForeground {
            completionHandler([])
            return
        }
        if #available(iOS 14.0, *) {
            completionHandler([.banner, .sound, .list])
        } else {
            completionHandler([.alert, .sound])
        }
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        CuttleNotifications.handleAction(response: response)
        completionHandler()
    }

    func application(_ application: UIApplication, performFetchWithCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void) {
        CuttleEventStream.shared.apply()
        completionHandler(.newData)
    }

    func application(_ app: UIApplication, open url: URL, options: [UIApplication.OpenURLOptionsKey: Any] = [:]) -> Bool {
        return ApplicationDelegateProxy.shared.application(app, open: url, options: options)
    }

    func application(_ application: UIApplication, continue userActivity: NSUserActivity, restorationHandler: @escaping ([UIUserActivityRestoring]?) -> Void) -> Bool {
        return ApplicationDelegateProxy.shared.application(application, continue: userActivity, restorationHandler: restorationHandler)
    }

}
