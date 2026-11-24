import SwiftUI
import UniformTypeIdentifiers

struct DashboardView: View {
    @EnvironmentObject private var store: SnapshotStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedProject: Project?
    @State private var showsMap = true
    @State private var showsSources = false
    @State private var showsImporter = false
    @State private var pendingImport = false

    private var activeCount: Int { store.snapshot.projects.reduce(0) { $0 + $1.activeAgentCount } }
    private var waitingCount: Int {
        store.snapshot.projects.flatMap(\.agents).filter { $0.status == .waiting }.count
    }
    private var cloudTaskCount: Int { store.snapshot.projects.reduce(0) { $0 + $1.tasks.count } }
    private var hasKnownAgents: Bool { store.snapshot.projects.contains { !$0.agents.isEmpty } }
    private var hasCloudProjects: Bool { store.snapshot.projects.contains { $0.isCloudSnapshot } }
    private var hasBriefProvenance: Bool {
        store.snapshot.projects.contains { $0.briefUpdatedAt != nil || $0.briefSource != nil }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 26) {
                    header
                    introduction
                    activitySummary

                    if !store.snapshot.projects.isEmpty {
                        Picker("Вид проектов", selection: $showsMap) {
                            Text("3D-карта").tag(true)
                            Text("Список").tag(false)
                        }
                        .pickerStyle(.segmented)

                        if showsMap {
                            VStack(alignment: .leading, spacing: 12) {
                                ProjectSceneView(
                                    projects: store.snapshot.projects,
                                    selectedProjectID: selectedProject?.id,
                                    reduceMotion: reduceMotion || scenePhase != .active || selectedProject != nil || showsSources || showsImporter,
                                    onSelect: selectProject
                                )
                                .frame(height: 360)
                                .clipShape(RoundedRectangle(cornerRadius: 28))
                                .accessibilityLabel("3D-карта проектов")
                                .accessibilityHint("Все проекты также доступны в списке под картой.")
                                Text("Коснись проекта, чтобы увидеть подробности")
                                    .font(.footnote)
                                    .foregroundStyle(OrbitTheme.secondary)
                            }
                        }

                        projectList
                        recentReportFeed
                    } else {
                        emptySnapshot
                    }
                    sourceTimestamp
                }
                .padding(.horizontal, 22)
                .padding(.top, 16)
                .padding(.bottom, 30)
            }
            .refreshable { if store.hasConnection { await store.refresh() } }
            .background(OrbitTheme.background)
            .toolbar(.hidden, for: .navigationBar)
            .sheet(item: $selectedProject) { project in
                ProjectDetailView(project: project)
                    .safeAreaInset(edge: .bottom) {
                        Label(detailSourceLabel(for: project), systemImage: store.isDemo ? "sparkles" : "doc")
                            .font(.caption)
                            .foregroundStyle(OrbitTheme.secondary)
                            .padding(12)
                            .frame(maxWidth: .infinity)
                            .background(OrbitTheme.background)
                    }
                    .presentationDetents([.large])
            }
            .sheet(isPresented: $showsSources, onDismiss: {
                if pendingImport { pendingImport = false; showsImporter = true }
            }) {
                DataSourceView(onImport: {
                    pendingImport = true
                    showsSources = false
                }, onDemo: {
                    store.useDemo()
                    showsSources = false
                })
            }
            .fileImporter(isPresented: $showsImporter, allowedContentTypes: [.json]) { result in
                switch result {
                case .success(let url): store.importSnapshot(from: url)
                case .failure(let error): store.errorMessage = error.localizedDescription
                }
            }
            .alert("Источник данных", isPresented: Binding(
                get: { store.errorMessage != nil },
                set: { if !$0 { store.errorMessage = nil } }
            )) {
                Button("Понятно", role: .cancel) { store.errorMessage = nil }
            } message: { Text(store.errorMessage ?? "") }
            .task(id: scenePhase) {
                guard scenePhase == .active else { return }
                while !Task.isCancelled {
                    await store.refreshIfConnected()
                    do { try await Task.sleep(for: .seconds(60)) }
                    catch { return }
                }
            }
            .onChange(of: store.snapshot.generatedAt) { _, _ in
                guard let selectedID = selectedProject?.id else { return }
                selectedProject = store.snapshot.projects.first { $0.id == selectedID }
            }
        }
        .tint(OrbitTheme.accent)
    }

    private var header: some View {
        HStack(spacing: 10) {
            HStack(spacing: 9) {
                Image(systemName: "circle.hexagongrid.fill")
                    .foregroundStyle(OrbitTheme.accent)
                Text("ORBIT").tracking(4).font(.headline)
            }
            Spacer(minLength: 4)
            if store.hasConnection {
                Button {
                    Task { await store.refresh() }
                } label: {
                    Group {
                        if store.isRefreshing { ProgressView() }
                        else { Image(systemName: "arrow.clockwise") }
                    }
                    .frame(width: 44, height: 44)
                }
                .disabled(store.isRefreshing)
                .accessibilityLabel(store.isRefreshing ? "Обновление снимка" : "Обновить облачные задачи")
            }
            Button { showsSources = true } label: {
                Label(store.sourceTitle, systemImage: store.hasConnection ? "cloud" : "externaldrive")
                    .font(.subheadline.weight(.medium))
                    .padding(.horizontal, 14)
                    .frame(minHeight: 44)
                    .background(OrbitTheme.surface, in: Capsule())
            }
            .accessibilityLabel("Источник данных: \(store.sourceTitle). Настроить.")
        }
    }

    private var introduction: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Твои проекты.\nВ движении.")
                .font(.largeTitle.bold())
                .foregroundStyle(.white)
            Text("Агенты, результаты и следующий шаг —\nв одном пространстве.")
                .font(.subheadline)
                .foregroundStyle(OrbitTheme.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var activitySummary: some View {
        VStack(alignment: .leading, spacing: 8) {
            if hasKnownAgents {
                Label("Агенты в сводках: \(activeCount) работают", systemImage: "bolt.fill")
                    .foregroundStyle(OrbitTheme.accent)
                HStack(spacing: 16) {
                    if waitingCount > 0 {
                        Label("Ждут: \(waitingCount)", systemImage: "pause.circle")
                            .foregroundStyle(Color(hex: "FFCA88"))
                    }
                    if hasCloudProjects {
                        Label("Задач Codex: \(cloudTaskCount)", systemImage: "cloud")
                            .foregroundStyle(OrbitTheme.secondary)
                    }
                }
            } else if hasCloudProjects || store.snapshot.projects.isEmpty {
                Label("Облачных задач: \(cloudTaskCount)", systemImage: "cloud")
                    .foregroundStyle(OrbitTheme.accent)
            } else {
                Label("Состав агентов не указан", systemImage: "person.crop.circle.badge.questionmark")
                    .foregroundStyle(OrbitTheme.secondary)
            }
        }
        .font(.subheadline)
        .accessibilityElement(children: .combine)
    }

    private var projectList: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Проекты").font(.title2.bold())
                Spacer()
                Text(store.snapshot.projects.count, format: .number)
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(OrbitTheme.secondary)
            }
            ForEach(store.snapshot.projects) { project in
                Button { selectedProject = project } label: {
                    ProjectRow(project: project)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(projectAccessibilityLabel(project))
            }
        }
    }

    @ViewBuilder
    private var recentReportFeed: some View {
        let entries = store.snapshot.recentReports()
        if !entries.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                Text("Последние отчёты")
                    .font(.title2.bold())
                    .accessibilityAddTraits(.isHeader)
                ForEach(entries) { entry in
                    Button { selectedProject = entry.project } label: {
                        RecentReportRow(entry: entry)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Открыть проект с этим отчётом")
                }
            }
        }
    }

    private var emptySnapshot: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: "cloud")
                .font(.largeTitle)
                .foregroundStyle(OrbitTheme.accent)
            Text("Облачных задач пока нет")
                .font(.title2.bold())
            Text("Источник вернул пустой список. Новые задачи появятся после следующего успешного обновления.")
                .font(.subheadline)
                .foregroundStyle(OrbitTheme.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(22)
        .background(OrbitTheme.surface, in: RoundedRectangle(cornerRadius: 24))
    }

    private var sourceTimestamp: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(store.isDemo ? "Демонстрационный снимок" : "Снимок собран", systemImage: "clock")
            Text(store.snapshot.generatedAt, format: .dateTime.day().month(.wide).year().hour().minute())
                .monospacedDigit()
            if let fetchedAt = store.lastFetchedAt {
                Text("Получен ") + Text(fetchedAt, format: .dateTime.hour().minute().second())
            }
            if let notice = store.connectionNotice {
                Label(notice, systemImage: "info.circle")
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.updatesFrequently)
            }
            if store.hasConnection {
                Text("Обновление примерно раз в минуту, пока приложение открыто. Можно потянуть экран вниз для обновления.")
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(hasBriefProvenance
                 ? "Облачные задачи и сводки агентов обновляются отдельно. Автор и время сводки указаны в карточке проекта; анимация отражает её состояния."
                 : "Статусы относятся к моменту снимка. Анимация иллюстрирует только переданные состояния агентов.")
                .fixedSize(horizontal: false, vertical: true)
        }
        .font(.footnote)
        .foregroundStyle(OrbitTheme.secondary)
    }

    private func selectProject(_ id: String) {
        selectedProject = store.snapshot.projects.first { $0.id == id }
    }

    private func detailSourceLabel(for project: Project) -> String {
        if store.isDemo { return "Демонстрационные данные" }
        if project.isCloudSnapshot, project.briefUpdatedAt != nil {
            return "Облачные задачи и сводка агентов"
        }
        return project.isCloudSnapshot ? "Снимок задач облачного Codex" : "Данные из импортированного снимка"
    }

    private func projectAccessibilityLabel(_ project: Project) -> String {
        let progress = project.progress.map { "Готовность \(Int(($0 * 100).rounded())) процентов." } ?? "Прогресс не указан."
        let work = project.isCloudSnapshot
            ? "Облачных задач: \(project.tasks.count)."
            : "Агентов работают: \(project.activeAgentCount)."
        let brief = project.briefUpdatedAt.map {
            "Сводка агентов от \($0.formatted(.dateTime.day().month(.wide).year().hour().minute().locale(Locale(identifier: "ru_RU"))))."
        } ?? ""
        return "\(project.name). \(project.stageTitle). \(progress) \(work) \(brief) Открыть проект."
    }
}

private struct RecentReportRow: View {
    let entry: ProjectReportEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(entry.project.name)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(Color(hex: entry.project.tintHex))
                Spacer(minLength: 4)
                Text(entry.report.createdAt, format: .dateTime.day().month(.abbreviated).hour().minute())
                    .font(.caption)
                    .foregroundStyle(OrbitTheme.secondary)
                    .multilineTextAlignment(.trailing)
            }
            Text(entry.report.title)
                .font(.headline)
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity, alignment: .leading)
                .lineLimit(1)
            if !entry.report.summary.isEmpty {
                Text(entry.report.summary)
                    .font(.subheadline)
                    .foregroundStyle(OrbitTheme.secondary)
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(OrbitTheme.surface, in: RoundedRectangle(cornerRadius: 22))
        .accessibilityElement(children: .combine)
    }
}

private struct ProjectRow: View {
    let project: Project
    private var tint: Color { Color(hex: project.tintHex) }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                Image(systemName: project.symbol)
                    .font(.title3)
                    .foregroundStyle(tint)
                    .frame(width: 46, height: 46)
                    .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 15))
                VStack(alignment: .leading, spacing: 5) {
                    Text(project.name).font(.headline).foregroundStyle(.white)
                    Text(project.stageTitle).font(.caption).foregroundStyle(OrbitTheme.secondary)
                }
                Spacer(minLength: 4)
                Image(systemName: "arrow.up.right").foregroundStyle(OrbitTheme.secondary)
            }
            if let progress = project.progress {
                ProgressView(value: progress)
                    .tint(tint)
                    .accessibilityHidden(true)
            }
            HStack(alignment: .top) {
                if project.isCloudSnapshot {
                    Label("Задач: \(project.tasks.count)", systemImage: "cloud")
                } else {
                    Label("Работают: \(project.activeAgentCount)", systemImage: "person.2")
                }
                Spacer()
                if let progress = project.progress {
                    Text(progress, format: .percent.precision(.fractionLength(0)))
                        .monospacedDigit().foregroundStyle(tint)
                } else {
                    Text("Прогресс не указан")
                        .multilineTextAlignment(.trailing)
                }
            }
            .font(.caption)
            .foregroundStyle(OrbitTheme.secondary)

            if let observedAt = project.briefUpdatedAt {
                VStack(alignment: .leading, spacing: 5) {
                    Label {
                        Text("Сводка от ") + Text(observedAt, format: .dateTime.day().month(.abbreviated).hour().minute())
                    } icon: {
                        Image(systemName: "doc.text")
                    }
                    if let source = project.briefSource { Text(source) }
                }
                .font(.caption)
                .foregroundStyle(OrbitTheme.secondary)
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(OrbitTheme.surface, in: RoundedRectangle(cornerRadius: 22))
    }
}
