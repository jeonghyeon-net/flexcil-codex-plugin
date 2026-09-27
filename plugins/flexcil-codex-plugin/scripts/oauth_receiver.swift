import AppKit
import CoreServices
import Darwin

// A local callback receiver for an explicitly started PKCE connection. It never
// reads another application's tokens and does not contact a remote service.
final class Receiver: NSObject, NSApplicationDelegate {
    func application(_ app: NSApplication, open urls: [URL]) {
        do {
            guard let folder = Bundle.main.object(forInfoDictionaryKey: "ConnectionDirectory") as? String else { return }
            let directory = URL(fileURLWithPath: folder)
            let raw = try Data(contentsOf: directory.appendingPathComponent("pending.json"))
            guard let pending = try JSONSerialization.jsonObject(with: raw) as? [String: Any],
                  let expectedState = pending["state"] as? String,
                  let redirect = pending["redirect_uri"] as? String,
                  let start = pending["created_at"] as? Double,
                  Date().timeIntervalSince1970 - start >= 0,
                  Date().timeIntervalSince1970 - start < 900,
                  let expected = URLComponents(string: redirect) else { return }
            for url in urls {
                guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
                      parts.scheme == expected.scheme,
                      parts.host == expected.host,
                      parts.path == expected.path,
                      parts.fragment == nil else { continue }
                let states = (parts.queryItems ?? []).filter { $0.name == "state" }
                guard states.count == 1, states[0].value == expectedState else { continue }
                umask(0o077)
                try Data(url.absoluteString.utf8).write(to: directory.appendingPathComponent("callback.txt"), options: .withoutOverwriting)
            }
        } catch { /* Do not log callback URLs or authorization codes. */ }
        app.terminate(nil)
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) { NSApp.terminate(nil) }
    }
}

if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--register" {
    let path = URL(fileURLWithPath: CommandLine.arguments[2])
    let result = LSRegisterURL(path as CFURL, true)
    print("registration_status=\(result)")
    exit(result == 0 ? 0 : 1)
}
if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--handler" {
    if let handler = LSCopyDefaultHandlerForURLScheme(CommandLine.arguments[2] as CFString)?.takeRetainedValue() {
        print(handler)
    } else { print("none") }
    exit(0)
}
if CommandLine.arguments.count == 4 && CommandLine.arguments[1] == "--select-handler" {
    let result = LSSetDefaultHandlerForURLScheme(CommandLine.arguments[2] as CFString, CommandLine.arguments[3] as CFString)
    exit(result == 0 ? 0 : 1)
}
let delegate = Receiver()
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
app.delegate = delegate
app.run()
