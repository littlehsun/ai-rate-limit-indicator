import AppKit
import Foundation

let fiveHourMinutes = 300
let weeklyMinutes = 10080

struct RateWindow {
    let usedPercent: Int
    let windowMinutes: Int
    let resetsAt: Int
}

struct RateSnapshot {
    let updatedAt: String
    let fiveHour: RateWindow?
    let weekly: RateWindow?
    let planType: String?
    let sourcePath: String?
}

func defaultCodexHome() -> URL {
    let env = ProcessInfo.processInfo.environment["CODEX_HOME"]
    if let env, !env.isEmpty {
        return URL(fileURLWithPath: NSString(string: env).expandingTildeInPath)
    }
    return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex")
}

func latestSnapshot(codexHome: URL) -> RateSnapshot? {
    let sessions = codexHome.appendingPathComponent("sessions")
    guard let enumerator = FileManager.default.enumerator(
        at: sessions,
        includingPropertiesForKeys: nil,
        options: [.skipsHiddenFiles]
    ) else {
        return nil
    }

    var latest: RateSnapshot?
    for case let fileURL as URL in enumerator {
        guard fileURL.lastPathComponent.hasPrefix("rollout-"),
              fileURL.pathExtension == "jsonl" else {
            continue
        }
        guard let snapshot = latestSnapshot(in: fileURL) else {
            continue
        }
        if latest == nil || snapshot.updatedAt > latest!.updatedAt {
            latest = snapshot
        }
    }
    return latest
}

func latestSnapshot(in fileURL: URL) -> RateSnapshot? {
    guard let content = try? String(contentsOf: fileURL, encoding: .utf8) else {
        return nil
    }

    var latest: RateSnapshot?
    for line in content.split(separator: "\n", omittingEmptySubsequences: true) {
        guard let data = String(line).data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              object["type"] as? String == "event_msg",
              let timestamp = object["timestamp"] as? String,
              let payload = object["payload"] as? [String: Any],
              let rateLimits = payload["rate_limits"] as? [String: Any] else {
            continue
        }

        let snapshot = snapshotFromRateLimits(
            timestamp: timestamp,
            rateLimits: rateLimits,
            sourcePath: fileURL.path
        )
        if latest == nil || snapshot.updatedAt > latest!.updatedAt {
            latest = snapshot
        }
    }
    return latest
}

func snapshotFromRateLimits(timestamp: String, rateLimits: [String: Any], sourcePath: String?) -> RateSnapshot {
    let windows = [
        parseWindow(rateLimits["primary"]),
        parseWindow(rateLimits["secondary"])
    ]
    let fiveHour = windows.compactMap { $0 }.first { $0.windowMinutes == fiveHourMinutes }
    let weekly = windows.compactMap { $0 }.first { $0.windowMinutes == weeklyMinutes }
    return RateSnapshot(
        updatedAt: timestamp,
        fiveHour: fiveHour,
        weekly: weekly,
        planType: rateLimits["plan_type"] as? String,
        sourcePath: sourcePath
    )
}

func parseWindow(_ value: Any?) -> RateWindow? {
    guard let dict = value as? [String: Any],
          let used = numberValue(dict["used_percent"]),
          let window = numberValue(dict["window_minutes"]),
          let reset = numberValue(dict["resets_at"]) else {
        return nil
    }
    return RateWindow(
        usedPercent: Int(used.rounded()),
        windowMinutes: Int(window),
        resetsAt: Int(reset)
    )
}

func numberValue(_ value: Any?) -> Double? {
    if let value = value as? Double {
        return value
    }
    if let value = value as? Int {
        return Double(value)
    }
    if let value = value as? NSNumber {
        return value.doubleValue
    }
    return nil
}

func indicatorLabel(snapshot: RateSnapshot, now: Int = Int(Date().timeIntervalSince1970)) -> String {
    let five = snapshot.fiveHour?.usedPercent ?? 0
    let weekly = snapshot.weekly?.usedPercent ?? 0
    let reset = snapshot.fiveHour.map { countdown(resetAt: $0.resetsAt, now: now) } ?? "--"
    return "\(five)%|\(weekly)% reset \(reset)"
}

func compactLabel(snapshot: RateSnapshot) -> String {
    let five = snapshot.fiveHour?.usedPercent ?? 0
    let weekly = snapshot.weekly?.usedPercent ?? 0
    return "\(five)%|\(weekly)%"
}

func countdown(resetAt: Int, now: Int = Int(Date().timeIntervalSince1970)) -> String {
    let seconds = resetAt - now
    if seconds <= 0 {
        return "soon"
    }
    let days = seconds / 86400
    let hours = (seconds % 86400) / 3600
    let minutes = (seconds % 3600) / 60
    if days > 0 {
        return "\(days)d\(hours)h"
    }
    if hours > 0 {
        return "\(hours)h\(minutes)m"
    }
    return "\(minutes)m"
}

func menuLine(_ window: RateWindow?, label: String, now: Int = Int(Date().timeIntervalSince1970)) -> String {
    guard let window else {
        return "\(label): no data"
    }
    let formatter = DateFormatter()
    formatter.dateFormat = label == "Weekly" ? "MM/dd HH:mm" : "HH:mm"
    let resetTime = formatter.string(from: Date(timeIntervalSince1970: TimeInterval(window.resetsAt)))
    return "\(label): \(window.usedPercent)% reset \(resetTime) (\(countdown(resetAt: window.resetsAt, now: now)))"
}

func formattedUpdatedAt(_ updatedAt: String, timeZone: TimeZone = .current) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

    var date = formatter.date(from: updatedAt)
    if date == nil {
        formatter.formatOptions = [.withInternetDateTime]
        date = formatter.date(from: updatedAt)
    }

    guard let parsedDate = date else {
        return String(updatedAt.replacingOccurrences(of: "T", with: " ").prefix(16))
    }

    let output = DateFormatter()
    output.dateFormat = "yyyy-MM-dd HH:mm"
    output.locale = Locale(identifier: "en_US_POSIX")
    output.timeZone = timeZone
    return output.string(from: parsedDate)
}

final class CodexRateApp: NSObject, NSApplicationDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let codexHome: URL
    private var timer: Timer?
    private let item5h = NSMenuItem(title: "5h: --", action: nil, keyEquivalent: "")
    private let itemWeekly = NSMenuItem(title: "Weekly: --", action: nil, keyEquivalent: "")
    private let itemUpdated = NSMenuItem(title: "Updated: --", action: nil, keyEquivalent: "")

    init(codexHome: URL) {
        self.codexHome = codexHome
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        item5h.isEnabled = false
        itemWeekly.isEnabled = false
        itemUpdated.isEnabled = false
        menu.addItem(item5h)
        menu.addItem(itemWeekly)
        menu.addItem(itemUpdated)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Refresh", action: #selector(refresh), keyEquivalent: "r"))
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(quit), keyEquivalent: "q"))
        statusItem.menu = menu

        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    @objc private func refresh() {
        guard let snapshot = latestSnapshot(codexHome: codexHome) else {
            statusItem.button?.title = "--"
            item5h.title = "5h: no data"
            itemWeekly.title = "Weekly: no data"
            itemUpdated.title = "Updated: --"
            return
        }

        let now = Int(Date().timeIntervalSince1970)
        statusItem.button?.title = compactLabel(snapshot: snapshot)
        item5h.title = menuLine(snapshot.fiveHour, label: "5h", now: now)
        itemWeekly.title = menuLine(snapshot.weekly, label: "Weekly", now: now)
        itemUpdated.title = "Updated: \(formattedUpdatedAt(snapshot.updatedAt))"
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }
}

func parseArgs() -> (once: Bool, codexHome: URL, now: Int?) {
    var once = false
    var codexHome = defaultCodexHome()
    var now: Int?
    var index = 1
    let args = CommandLine.arguments
    while index < args.count {
        switch args[index] {
        case "--once":
            once = true
        case "--codex-home":
            if index + 1 < args.count {
                index += 1
                codexHome = URL(fileURLWithPath: NSString(string: args[index]).expandingTildeInPath)
            }
        case "--now":
            if index + 1 < args.count {
                index += 1
                now = Int(args[index])
            }
        default:
            break
        }
        index += 1
    }
    return (once, codexHome, now)
}

let args = parseArgs()
if args.once {
    if let snapshot = latestSnapshot(codexHome: args.codexHome) {
        print(compactLabel(snapshot: snapshot))
        exit(0)
    }
    print("--")
    exit(1)
}

let app = NSApplication.shared
let delegate = CodexRateApp(codexHome: args.codexHome)
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
