import Foundation

enum SnapshotCodec {
    static let maximumBytes = 2 * 1024 * 1024

    static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let value = try container.decode(String.self)
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = formatter.date(from: value) { return date }
            formatter.formatOptions = [.withInternetDateTime]
            guard let date = formatter.date(from: value) else {
                throw DecodingError.dataCorruptedError(in: container, debugDescription: "Expected ISO 8601 date")
            }
            return date
        }
        return decoder
    }

    static func decode(_ data: Data) throws -> ProjectSnapshot {
        guard data.count <= maximumBytes else { throw SnapshotError.tooLarge }
        let snapshot: ProjectSnapshot
        do { snapshot = try decoder().decode(ProjectSnapshot.self, from: data) }
        catch { throw SnapshotError.invalid("Нужен JSON-снимок в формате Orbit. Проверьте структуру и даты ISO 8601.") }
        try validate(snapshot)
        return snapshot
    }

    static func validate(_ snapshot: ProjectSnapshot) throws {
        guard snapshot.projects.count <= 12 else { throw SnapshotError.invalid("Поддерживается до 12 проектов.") }
        try uniqueIDs(snapshot.projects.map(\.id))
        for project in snapshot.projects {
            guard (project.briefUpdatedAt == nil) == (project.briefSource == nil) else {
                throw SnapshotError.invalid("У сводки агентов должны быть указаны и источник, и время наблюдения.")
            }
            if let producer = project.briefSource,
               producer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || producer.count > 80 {
                throw SnapshotError.invalid("Источник сводки должен содержать от 1 до 80 символов.")
            }
            if let progress = project.progress, !progress.isFinite || !(0...1).contains(progress) {
                throw SnapshotError.invalid("Прогресс должен быть числом от 0 до 1 или null, если он неизвестен.")
            }
            guard !project.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  project.name.count <= 80,
                  project.tintHex.count == 6, UInt32(project.tintHex, radix: 16) != nil,
                  project.agents.count <= 12, project.reports.count <= 50,
                  project.steps.count <= 50, project.tasks.count <= 100 else {
                throw SnapshotError.invalid("Проверьте имя, цвет и количество записей проекта «\(project.name.prefix(80))».")
            }
            try uniqueIDs(project.agents.map(\.id))
            try uniqueIDs(project.reports.map(\.id))
            try uniqueIDs(project.steps.map(\.id))
            try uniqueIDs(project.tasks.map(\.id))
            for task in project.tasks {
                guard !task.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                      task.title.count <= 300, !task.status.isEmpty, task.status.count <= 80 else {
                    throw SnapshotError.invalid("Проверьте заголовок и статус облачной задачи.")
                }
                if let url = task.url, task.safeURL != url {
                    throw SnapshotError.invalid("Ссылка облачной задачи должна вести на https://chatgpt.com без учётных данных.")
                }
            }
        }
    }

    private static func uniqueIDs(_ ids: [String]) throws {
        guard Set(ids).count == ids.count,
              ids.allSatisfy({ !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && $0.count <= 120 }) else {
            throw SnapshotError.invalid("Идентификаторы должны быть непустыми и уникальными внутри своего списка, не длиннее 120 символов.")
        }
    }
}

enum SnapshotError: LocalizedError {
    case storage, tooLarge
    case invalid(String)

    var errorDescription: String? {
        switch self {
        case .storage: return "Хранилище приложения недоступно."
        case .tooLarge: return "Максимальный размер снимка — 2 МБ."
        case .invalid(let text): return text
        }
    }
}
