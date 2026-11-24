import Foundation

enum AgentStatus: String, Codable, CaseIterable {
    case working
    case waiting
    case completed

    var title: String {
        switch self {
        case .working: return "Работает"
        case .waiting: return "Ожидает решения"
        case .completed: return "Завершил работу"
        }
    }
}

enum ProjectStage: String, Codable, CaseIterable {
    case discovery
    case development
    case testing
    case delivery

    var title: String {
        switch self {
        case .discovery: return "Исследование"
        case .development: return "Разработка"
        case .testing: return "Проверка"
        case .delivery: return "Передача результата"
        }
    }

    var symbol: String {
        switch self {
        case .discovery: return "sparkle.magnifyingglass"
        case .development: return "hammer.fill"
        case .testing: return "checkmark.shield.fill"
        case .delivery: return "shippingbox.fill"
        }
    }
}

struct ProjectAgent: Identifiable, Codable {
    let id: String
    let name: String
    let role: String
    let status: AgentStatus
    let currentTask: String
}

struct ProjectReport: Identifiable, Codable {
    let id: String
    let title: String
    let summary: String
    let createdAt: Date
    let highlights: [String]
}

struct ProjectStep: Identifiable, Codable {
    let id: String
    let title: String
    let detail: String
    let isComplete: Bool
}

/// A cloud task is distinct from a project agent. Its status is supplied verbatim by Codex.
struct CloudTask: Identifiable, Codable {
    let id: String
    let title: String
    let status: String
    let url: URL?
    let updatedAt: Date?
    let summary: String?

    /// Only official HTTPS task links may leave the app; imported links are untrusted.
    var safeURL: URL? {
        guard let url,
              url.scheme?.lowercased() == "https",
              url.user == nil, url.password == nil,
              url.port == nil || url.port == 443,
              let host = url.host?.lowercased(),
              host == "chatgpt.com" || host == "www.chatgpt.com" else { return nil }
        return url
    }
}

struct Project: Identifiable, Codable {
    let id: String
    let name: String
    let subtitle: String
    let symbol: String
    let tintHex: String
    /// Missing values mean the source does not report this information.
    let stage: ProjectStage?
    /// A fraction in 0...1 supplied by the project's data source; never inferred from task status.
    let progress: Double?
    let updatedAt: Date?
    let agents: [ProjectAgent]
    let reports: [ProjectReport]
    let steps: [ProjectStep]
    var cloudTasks: [CloudTask]? = nil
    var source: String? = nil
    /// Time and author of the explicit project brief, separate from cloud polling.
    var briefUpdatedAt: Date? = nil
    var briefSource: String? = nil

    var stageTitle: String { stage?.title ?? "Этап не указан" }

    var progressLabel: String? {
        guard let progress, progress.isFinite, (0...1).contains(progress) else { return nil }
        return "\(Int((progress * 100).rounded()))%"
    }

    var tasks: [CloudTask] { cloudTasks ?? [] }

    var isCloudSnapshot: Bool { source == "codex-cloud" }

    var activeAgentCount: Int {
        agents.filter { $0.status == .working }.count
    }

    var completedStepCount: Int {
        steps.filter(\.isComplete).count
    }
}

struct ProjectSnapshot: Codable {
    let generatedAt: Date
    let projects: [Project]

    func recentReports(limit: Int = 3) -> [ProjectReportEntry] {
        guard limit > 0 else { return [] }
        let entries = projects.flatMap { project in
            project.reports.map { ProjectReportEntry(project: project, report: $0) }
        }
        let sorted = entries.sorted { left, right in
            if left.report.createdAt != right.report.createdAt {
                return left.report.createdAt > right.report.createdAt
            }
            if left.project.id != right.project.id {
                return left.project.id < right.project.id
            }
            return left.report.id < right.report.id
        }
        return Array(sorted.prefix(limit))
    }
}

struct ProjectReportEntry: Identifiable {
    struct ID: Hashable {
        let projectID: String
        let reportID: String
    }

    let project: Project
    let report: ProjectReport

    var id: ID { ID(projectID: project.id, reportID: report.id) }
}
