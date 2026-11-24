import Foundation
import Combine

enum SnapshotOrigin: String, Codable {
    case demo, imported, remote
}

private struct PersistedSnapshot: Codable {
    let snapshot: ProjectSnapshot
    let origin: SnapshotOrigin
    let fetchedAt: Date?
    let syncEnabled: Bool?
}

@MainActor
final class SnapshotStore: ObservableObject {
    @Published private(set) var snapshot = DemoData.snapshot
    @Published private(set) var origin: SnapshotOrigin = .demo
    @Published private(set) var isRefreshing = false
    @Published private(set) var lastFetchedAt: Date?
    @Published private(set) var connectionNotice: String?
    @Published var errorMessage: String?
    @Published private var connection: SnapshotConnection?
    @Published private var syncEnabled = false

    private let savedURL: URL?
    private var refreshTask: Task<Void, Never>?
    private var revision = 0

    var isDemo: Bool { origin == .demo }
    var hasConnection: Bool { connection != nil && syncEnabled }
    var hasStoredConnection: Bool { connection != nil }
    var connectionDisplayURL: String { connection?.endpoint.absoluteString ?? "" }
    var sourceTitle: String {
        switch origin {
        case .demo: return "Демо"
        case .imported: return "Снимок"
        case .remote: return "Облако"
        }
    }

    init() {
        savedURL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first?.appendingPathComponent("Orbit", isDirectory: true)
            .appendingPathComponent("snapshot.json")
        if let savedURL, FileManager.default.fileExists(atPath: savedURL.path) {
            do {
                let data = try Self.readBounded(savedURL)
                if let saved = try? SnapshotCodec.decoder().decode(PersistedSnapshot.self, from: data) {
                    try SnapshotCodec.validate(saved.snapshot)
                    snapshot = saved.snapshot
                    origin = saved.origin
                    lastFetchedAt = saved.fetchedAt
                    syncEnabled = saved.syncEnabled ?? (saved.origin == .remote)
                } else {
                    // Read the unwrapped format used by the first prototype.
                    snapshot = try SnapshotCodec.decode(data)
                    origin = .imported
                }
            } catch {
                errorMessage = "Не удалось открыть сохранённый снимок. Показаны демоданные. \(error.localizedDescription)"
            }
        }
        do { connection = try ConnectionKeychain.load() }
        catch { connectionNotice = "Настройки подключения недоступны. Откройте источник данных и проверьте подключение." }
    }

    @discardableResult
    func saveConnection(endpoint: String, token: String) -> Bool {
        do {
            let normalized = endpoint.trimmingCharacters(in: .whitespacesAndNewlines)
            // Do not reuse a secret for a different endpoint without explicit re-entry.
            let secret: String
            if token.isEmpty, let existing = connection, existing.endpoint.absoluteString == normalized {
                secret = existing.token
            } else { secret = token }
            let settings = try SnapshotConnection.validated(endpoint: normalized, token: secret)
            let previousConnection = connection
            try ConnectionKeychain.save(settings)
            do {
                // Keep automatic retries enabled across relaunch even if the first fetch fails.
                try persist(snapshot, origin: origin, fetchedAt: lastFetchedAt, syncEnabled: true)
            } catch {
                do {
                    if let previousConnection { try ConnectionKeychain.save(previousConnection) }
                    else { try ConnectionKeychain.delete() }
                } catch {
                    connectionNotice = "Не удалось восстановить предыдущие настройки. Проверьте подключение перед следующим запуском."
                }
                throw error
            }
            cancelRefresh()
            connection = settings
            syncEnabled = true
            connectionNotice = "Подключение сохранено. Получаем снимок…"
            Task { await refresh() }
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func disconnect() {
        do {
            try ConnectionKeychain.delete()
            cancelRefresh()
            connection = nil
            syncEnabled = false
            connectionNotice = "Подключение отключено. Последний снимок сохранён на iPhone."
        } catch { errorMessage = error.localizedDescription }
    }

    func refreshIfConnected() async {
        if hasConnection { await refresh() }
    }

    func refresh() async {
        guard hasConnection, let settings = connection else { return }
        if let refreshTask { await refreshTask.value; return }
        let startedAtRevision = revision
        isRefreshing = true
        let task: Task<Void, Never> = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.revision == startedAtRevision {
                    self.isRefreshing = false
                    self.refreshTask = nil
                }
            }
            do {
                let remote = try await SnapshotClient.fetch(settings)
                try Task.checkCancellation()
                guard self.revision == startedAtRevision else { return }
                let decoded = try SnapshotCodec.decode(remote.data)
                let fetchedAt = Date()
                try self.persist(decoded, origin: .remote, fetchedAt: fetchedAt, syncEnabled: true)
                self.snapshot = decoded
                self.origin = .remote
                self.lastFetchedAt = fetchedAt
                self.connectionNotice = remote.isStale
                    ? "Сервис не смог обновить Codex и вернул предыдущий снимок. Проверьте время данных."
                    : nil
            } catch is CancellationError {
                // A source change invalidates in-flight results.
            } catch {
                guard !Task.isCancelled, self.revision == startedAtRevision else { return }
                self.connectionNotice = "Не удалось обновить данные. \(error.localizedDescription)"
            }
        }
        refreshTask = task
        await task.value
    }

    func importSnapshot(from url: URL) {
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        do {
            let imported = try SnapshotCodec.decode(Self.readBounded(url))
            try persist(imported, origin: .imported, fetchedAt: nil, syncEnabled: false)
            cancelRefresh()
            syncEnabled = false
            snapshot = imported
            origin = .imported
            lastFetchedAt = nil
            connectionNotice = nil
        } catch { errorMessage = "Снимок не загружен. \(error.localizedDescription)" }
    }

    func useDemo() {
        do {
            try persist(DemoData.snapshot, origin: .demo, fetchedAt: nil, syncEnabled: false)
            cancelRefresh()
            syncEnabled = false
            snapshot = DemoData.snapshot
            origin = .demo
            lastFetchedAt = nil
            connectionNotice = nil
        } catch { errorMessage = "Не удалось переключить источник. \(error.localizedDescription)" }
    }

    private func cancelRefresh() {
        revision += 1
        refreshTask?.cancel()
        refreshTask = nil
        isRefreshing = false
    }

    private func persist(_ snapshot: ProjectSnapshot, origin: SnapshotOrigin, fetchedAt: Date?, syncEnabled: Bool) throws {
        guard let savedURL else { throw SnapshotError.storage }
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(PersistedSnapshot(snapshot: snapshot, origin: origin, fetchedAt: fetchedAt, syncEnabled: syncEnabled))
        guard data.count <= SnapshotCodec.maximumBytes else { throw SnapshotError.tooLarge }
        var directory = savedURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try directory.setResourceValues(values)
        try data.write(to: savedURL, options: [.atomic, .completeFileProtection])
    }

    private static func readBounded(_ url: URL) throws -> Data {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let data = try handle.read(upToCount: SnapshotCodec.maximumBytes + 1) ?? Data()
        guard data.count <= SnapshotCodec.maximumBytes else { throw SnapshotError.tooLarge }
        return data
    }
}
