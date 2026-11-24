import Foundation
import XCTest
@testable import OrbitCore

final class SnapshotCodecTests: XCTestCase {
    func testOriginalDemoSnapshotRemainsCompatible() throws {
        let snapshot = try SnapshotCodec.decode(fixture("example-snapshot"))

        XCTAssertEqual(snapshot.projects.count, 3)
        let analytics = try XCTUnwrap(snapshot.projects.first { $0.id == "analytics" })
        XCTAssertEqual(analytics.stage, .development)
        XCTAssertEqual(try XCTUnwrap(analytics.progress), 0.6, accuracy: 0.0001)
        XCTAssertEqual(analytics.activeAgentCount, 2)
        XCTAssertFalse(analytics.reports.isEmpty)
        XCTAssertTrue(analytics.tasks.isEmpty)
        XCTAssertNil(analytics.source)
        XCTAssertNil(analytics.briefUpdatedAt)
        XCTAssertNil(analytics.briefSource)
    }

    func testCloudSnapshotKeepsUnknownFieldsUnknown() throws {
        let snapshot = try SnapshotCodec.decode(fixture("example-cloud-snapshot"))
        let project = try XCTUnwrap(snapshot.projects.first)

        XCTAssertNil(project.stage)
        XCTAssertNil(project.progress)
        XCTAssertNil(project.progressLabel)
        XCTAssertNil(project.updatedAt)
        XCTAssertTrue(project.isCloudSnapshot)
        XCTAssertTrue(project.agents.isEmpty)
        XCTAssertTrue(project.reports.isEmpty)
        XCTAssertTrue(project.steps.isEmpty)
        XCTAssertEqual(project.tasks.count, 2)
        XCTAssertEqual(project.tasks.first?.status, "example_running")
        XCTAssertNil(project.tasks.first?.safeURL)
        XCTAssertNil(project.tasks.last?.updatedAt)
    }

    func testMissingOptionalFieldsDecodeWithoutInventingValues() throws {
        let snapshot = try SnapshotCodec.decode(document())
        let project = try XCTUnwrap(snapshot.projects.first)

        XCTAssertNil(project.stage)
        XCTAssertNil(project.progress)
        XCTAssertNil(project.updatedAt)
        XCTAssertNil(project.source)
        XCTAssertNil(project.cloudTasks)
        XCTAssertNil(project.briefUpdatedAt)
        XCTAssertNil(project.briefSource)
        XCTAssertTrue(project.tasks.isEmpty)
    }

    func testProjectBriefPreservesCloudTasksAndItsOwnObservationTime() throws {
        let task: [String: Any] = [
            "id": "cloud-task", "title": "Облачная задача", "status": "new_upstream_status",
            "updatedAt": "2026-11-15T10:25:00Z"
        ]
        let agents: [[String: Any]] = [[
            "id": "atlas", "name": "Атлас", "role": "Аналитик",
            "status": "working", "currentTask": "Проверяет выводы"
        ]]
        let reports: [[String: Any]] = [[
            "id": "report", "title": "Данные подготовлены", "summary": "Подготовлен контрольный набор.",
            "createdAt": "2026-11-15T10:10:00Z", "highlights": ["Проверены пропуски"]
        ]]
        let steps: [[String: Any]] = [[
            "id": "step", "title": "Проверить выводы", "detail": "Сравнить с контрольными примерами.",
            "isComplete": false
        ]]
        let data = try document(projectValues: [
            "source": "codex-cloud", "cloudTasks": [task],
            "briefUpdatedAt": "2026-11-15T10:15:00Z", "briefSource": "Команда аналитики",
            "stage": "testing", "progress": 0.6,
            "agents": agents, "reports": reports, "steps": steps
        ])
        let snapshot = try SnapshotCodec.decode(data)
        let project = try XCTUnwrap(snapshot.projects.first)
        let observedAt = try XCTUnwrap(project.briefUpdatedAt)

        XCTAssertTrue(project.isCloudSnapshot)
        XCTAssertEqual(project.briefSource, "Команда аналитики")
        XCTAssertLessThan(observedAt, snapshot.generatedAt)
        XCTAssertLessThan(observedAt, try XCTUnwrap(project.tasks.first?.updatedAt))
        XCTAssertEqual(project.tasks.first?.id, "cloud-task")
        XCTAssertEqual(project.tasks.first?.status, "new_upstream_status")
        XCTAssertEqual(project.activeAgentCount, 1)
        XCTAssertEqual(project.stage, .testing)
        XCTAssertEqual(project.reports.first?.id, "report")
        XCTAssertEqual(project.steps.first?.isComplete, false)

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let restored = try SnapshotCodec.decode(encoder.encode(snapshot))
        XCTAssertEqual(restored.projects.first?.briefUpdatedAt, observedAt)
        XCTAssertEqual(restored.projects.first?.briefSource, project.briefSource)
    }

    func testBriefObservationTimeAndProducerAreRequiredTogether() throws {
        assertInvalid(try document(projectValues: ["briefUpdatedAt": "2026-11-15T10:15:00Z"]))
        assertInvalid(try document(projectValues: ["briefSource": "Команда аналитики"]))
        assertInvalid(try document(projectValues: [
            "briefUpdatedAt": "2026-11-15T10:15:00Z", "briefSource": " \n "
        ]))
        assertInvalid(try document(projectValues: [
            "briefUpdatedAt": "2026-11-15T10:15:00Z", "briefSource": String(repeating: "a", count: 81)
        ]))
    }

    func testExplicitNullBriefMetadataMeansNoBrief() throws {
        let snapshot = try SnapshotCodec.decode(document(projectValues: [
            "briefUpdatedAt": NSNull(), "briefSource": NSNull()
        ]))

        XCTAssertNil(snapshot.projects.first?.briefUpdatedAt)
        XCTAssertNil(snapshot.projects.first?.briefSource)
    }

    func testEmptyProjectsAreAValidCloudResult() throws {
        let data = try JSONSerialization.data(withJSONObject: [
            "generatedAt": "2026-11-15T10:30:00Z",
            "projects": []
        ])

        XCTAssertTrue(try SnapshotCodec.decode(data).projects.isEmpty)
    }

    func testFractionalISO8601TimestampsDecode() throws {
        let data = try document(generatedAt: "2026-11-15T10:30:00.125Z")
        let snapshot = try SnapshotCodec.decode(data)

        XCTAssertEqual(
            snapshot.generatedAt.timeIntervalSince1970.truncatingRemainder(dividingBy: 1),
            0.125,
            accuracy: 0.001
        )
    }

    func testOutOfRangeProgressIsRejected() throws {
        for progress in [-0.01, 1.01] {
            assertInvalid(try document(projectValues: ["progress": progress]))
        }
    }

    func testBlankAndDuplicateProjectIDsAreRejected() throws {
        assertInvalid(try document(projectValues: ["id": " \n "]))

        let duplicate = project()
        let data = try JSONSerialization.data(withJSONObject: [
            "generatedAt": "2026-11-15T10:30:00Z",
            "projects": [duplicate, duplicate]
        ])
        assertInvalid(data)
    }

    func testDuplicateCloudTaskIDsAreRejected() throws {
        let task: [String: Any] = ["id": "task", "title": "Пример задачи", "status": "unknown"]
        assertInvalid(try document(projectValues: ["cloudTasks": [task, task]]))
    }

    func testUnknownUpstreamTaskStatusIsPreservedVerbatim() throws {
        let task: [String: Any] = [
            "id": "task",
            "title": "Пример задачи",
            "status": "new_upstream_status"
        ]
        let data = try document(projectValues: ["source": "codex-cloud", "cloudTasks": [task]])
        let snapshot = try SnapshotCodec.decode(data)
        let decoded = try XCTUnwrap(snapshot.projects.first?.tasks.first)

        XCTAssertEqual(decoded.status, "new_upstream_status")
        XCTAssertNil(decoded.url)
        XCTAssertNil(decoded.updatedAt)
        XCTAssertNil(decoded.summary)
    }

    func testUnsafeCloudLinksAreRejectedBeforeDisplay() throws {
        let addresses = [
            "http://chatgpt.com/codex/tasks/example",
            "https://chatgpt.com.evil.example/codex/tasks/example",
            "https://evil.chatgpt.com/codex/tasks/example",
            "https://user:secret@chatgpt.com/codex/tasks/example",
            "https://chatgpt.com:8443/codex/tasks/example"
        ]
        for address in addresses {
            let task: [String: Any] = [
                "id": "task", "title": "Пример задачи", "status": "unknown", "url": address
            ]
            assertInvalid(try document(projectValues: ["cloudTasks": [task]]))
        }
    }

    func testOfficialHTTPSCloudTaskLinkIsAvailable() throws {
        let address = "https://chatgpt.com/codex/tasks/example"
        let task: [String: Any] = [
            "id": "task", "title": "Пример задачи", "status": "unknown", "url": address
        ]
        let snapshot = try SnapshotCodec.decode(document(projectValues: ["cloudTasks": [task]]))
        let decoded = try XCTUnwrap(snapshot.projects.first?.tasks.first)

        XCTAssertEqual(decoded.safeURL?.absoluteString, address)
    }

    func testRecentReportsSortAcrossProjectsWithDeterministicTies() {
        let snapshot = ProjectSnapshot(generatedAt: Date(), projects: [
            feedProject(id: "b", reports: [feedReport(id: "older", time: 100), feedReport(id: "a", time: 200)]),
            feedProject(id: "a", reports: [feedReport(id: "z", time: 200), feedReport(id: "b", time: 200)])
        ])

        let entries = snapshot.recentReports(limit: 10)

        XCTAssertEqual(entries.map(\.project.id), ["a", "a", "b", "b"])
        XCTAssertEqual(entries.map(\.report.id), ["b", "z", "a", "older"])
        XCTAssertEqual(entries.last?.report.createdAt, Date(timeIntervalSince1970: 100))
    }

    func testRecentReportsRespectLimitsAndEmptySnapshots() {
        let snapshot = ProjectSnapshot(generatedAt: Date(), projects: [
            feedProject(id: "project", reports: (1...4).map { feedReport(id: String($0), time: Double($0)) })
        ])

        XCTAssertEqual(snapshot.recentReports().count, 3)
        XCTAssertEqual(snapshot.recentReports(limit: 1).first?.report.id, "4")
        XCTAssertEqual(snapshot.recentReports(limit: Int.max).count, 4)
        XCTAssertTrue(snapshot.recentReports(limit: 0).isEmpty)
        XCTAssertTrue(snapshot.recentReports(limit: -1).isEmpty)
        XCTAssertTrue(ProjectSnapshot(generatedAt: Date(), projects: []).recentReports().isEmpty)
        XCTAssertTrue(ProjectSnapshot(generatedAt: Date(), projects: [feedProject(id: "empty", reports: [])]).recentReports().isEmpty)
    }

    func testRecentReportIdentityIncludesBothIDsWithoutDelimiterCollisions() {
        let snapshot = ProjectSnapshot(generatedAt: Date(), projects: [
            feedProject(id: "a:b", reports: [feedReport(id: "c", time: 100), feedReport(id: "shared", time: 100)]),
            feedProject(id: "a", reports: [feedReport(id: "b:c", time: 100), feedReport(id: "shared", time: 100)])
        ])
        let entries = snapshot.recentReports(limit: 10)

        XCTAssertEqual(Set(entries.map(\.id)).count, 4)
        XCTAssertEqual(entries.first?.id, snapshot.recentReports(limit: 10).first?.id)
        XCTAssertNotEqual(
            ProjectReportEntry.ID(projectID: "a:b", reportID: "c"),
            ProjectReportEntry.ID(projectID: "a", reportID: "b:c")
        )
    }

    private func feedProject(id: String, reports: [ProjectReport]) -> Project {
        Project(id: id, name: id, subtitle: "", symbol: "doc.text", tintHex: "92F2C6",
                stage: nil, progress: nil, updatedAt: nil, agents: [], reports: reports, steps: [])
    }

    private func feedReport(id: String, time: TimeInterval) -> ProjectReport {
        ProjectReport(id: id, title: id, summary: "Пример отчёта", createdAt: Date(timeIntervalSince1970: time), highlights: [])
    }

    private func fixture(_ name: String) throws -> Data {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        return try Data(contentsOf: root.appendingPathComponent("AgentOrbit/Resources/\(name).json"))
    }

    private func project(values: [String: Any] = [:]) -> [String: Any] {
        var result: [String: Any] = [
            "id": "example-project",
            "name": "Пример проекта",
            "subtitle": "Данные для проверки формата",
            "symbol": "cloud",
            "tintHex": "92F2C6",
            "agents": [],
            "reports": [],
            "steps": []
        ]
        result.merge(values) { _, supplied in supplied }
        return result
    }

    private func document(
        projectValues: [String: Any] = [:],
        generatedAt: String = "2026-11-15T10:30:00Z"
    ) throws -> Data {
        try JSONSerialization.data(withJSONObject: [
            "generatedAt": generatedAt,
            "projects": [project(values: projectValues)]
        ])
    }

    private func assertInvalid(_ data: Data, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try SnapshotCodec.decode(data), file: file, line: line) { error in
            guard let snapshotError = error as? SnapshotError,
                  case .invalid = snapshotError else {
                return XCTFail("Expected an invalid snapshot error, got \(error)", file: file, line: line)
            }
        }
    }
}
