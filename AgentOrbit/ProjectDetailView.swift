import SwiftUI

struct ProjectDetailView: View {
    let project: Project

    @Environment(\.dismiss) private var dismiss
    @State private var expandedReportIDs: Set<String>

    init(project: Project) {
        self.project = project
        _expandedReportIDs = State(initialValue: Set(
            project.reports.sorted { $0.createdAt > $1.createdAt }.prefix(1).map(\.id)
        ))
    }

    private var tint: Color { Color(hex: project.tintHex) }
    private var progress: Double? {
        guard let value = project.progress, value.isFinite else { return nil }
        return min(max(value, 0), 1)
    }
    private var currentStageIndex: Int {
        guard let stage = project.stage else { return -1 }
        return ProjectStage.allCases.firstIndex(of: stage) ?? -1
    }
    private var hasBrief: Bool { project.briefUpdatedAt != nil || project.briefSource != nil }

    var body: some View {
        VStack(spacing: 0) {
            sheetHeader

            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    projectHeader
                    if hasBrief {
                        briefProvenance
                    }
                    progressSummary
                    stagesSection
                    if !project.tasks.isEmpty { cloudTasksSection }
                    agentsSection
                    reportsSection
                    stepsSection

                    if let updatedAt = project.updatedAt {
                        Label {
                            Text("Обновлено ")
                            + Text(updatedAt, format: .dateTime.day().month(.abbreviated).hour().minute())
                        } icon: {
                            Image(systemName: "clock")
                        }
                        .font(.caption)
                        .foregroundStyle(OrbitTheme.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    } else {
                        Label("Дата обновления проекта не указана", systemImage: "clock")
                            .font(.caption)
                            .foregroundStyle(OrbitTheme.secondary)
                    }
                }
                .padding(.horizontal, 22)
                .padding(.top, 12)
                .padding(.bottom, 36)
            }
        }
        .background(OrbitTheme.background)
        .foregroundStyle(.white)
        .presentationBackground(OrbitTheme.background)
        .preferredColorScheme(.dark)
    }

    private var sheetHeader: some View {
        HStack(spacing: 12) {
            Text("ОБЗОР ПРОЕКТА")
                .font(.caption.weight(.semibold))
                .tracking(2)
                .foregroundStyle(OrbitTheme.secondary)

            Spacer(minLength: 8)

            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.body.weight(.semibold))
                    .frame(width: 44, height: 44)
                    .background(.white.opacity(0.07), in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Закрыть проект")
        }
        .padding(.horizontal, 22)
        .padding(.top, 16)
        .padding(.bottom, 6)
    }

    private var projectHeader: some View {
        VStack(alignment: .leading, spacing: 14) {
            Image(systemName: project.symbol)
                .font(.title2)
                .foregroundStyle(tint)
                .frame(width: 54, height: 54)
                .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 17))
                .accessibilityHidden(true)

            Text(project.name)
                .font(.largeTitle.weight(.bold))
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)

            Text(project.subtitle)
                .font(.subheadline)
                .foregroundStyle(OrbitTheme.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var progressSummary: some View {
        HStack(spacing: 18) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Сейчас")
                    .font(.caption)
                    .foregroundStyle(OrbitTheme.secondary)

                Label(project.stageTitle, systemImage: project.stage?.symbol ?? "questionmark.circle")
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)

                if !project.steps.isEmpty {
                    Text("Шаги: \(project.completedStepCount) из \(project.steps.count)")
                        .font(.caption)
                        .foregroundStyle(OrbitTheme.secondary)
                } else if project.isCloudSnapshot {
                    Text("Источник не передал план работ")
                        .font(.caption)
                        .foregroundStyle(OrbitTheme.secondary)
                }
            }

            Spacer(minLength: 0)

            if let progress {
                ZStack {
                    Circle()
                        .stroke(tint.opacity(0.12), lineWidth: 6)
                    Circle()
                        .trim(from: 0, to: progress)
                        .stroke(tint, style: StrokeStyle(lineWidth: 6, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                    Text(progress, format: .percent.precision(.fractionLength(0)))
                        .font(.title3.weight(.semibold))
                        .monospacedDigit()
                }
                .frame(width: 76, height: 76)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Прогресс проекта")
                .accessibilityValue(Text(progress, format: .percent.precision(.fractionLength(0))))
            } else {
                VStack(spacing: 6) {
                    Image(systemName: "questionmark.circle")
                        .font(.title2)
                    Text("Прогресс не указан")
                        .font(.caption)
                        .multilineTextAlignment(.center)
                }
                .foregroundStyle(OrbitTheme.secondary)
                .frame(width: 86)
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(OrbitTheme.surface, in: RoundedRectangle(cornerRadius: 24))
    }

    private var briefProvenance: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Сводка агентов", systemImage: "doc.text")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(tint)
            if let observedAt = project.briefUpdatedAt {
                Text(observedAt, format: .dateTime.day().month(.wide).year().hour().minute())
                    .font(.subheadline)
                    .monospacedDigit()
            } else {
                Text("Дата сводки не указана")
                    .font(.subheadline)
            }
            if let source = project.briefSource {
                Text("Подготовил: \(source)")
                    .font(.caption)
            }
            Text(project.tasks.isEmpty
                 ? "Состояния агентов, этап и план относятся к этой сводке."
                 : "Состояния агентов, этап и план относятся к этой сводке. Облачные задачи ниже обновляются отдельно.")
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(OrbitTheme.secondary)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private var stagesSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            sectionTitle("Путь проекта", symbol: "point.topleft.down.to.point.bottomright.curvepath")

            if project.stage == nil {
                emptyState("Источник не сообщает этап проекта", symbol: "questionmark.circle")
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(ProjectStage.allCases.enumerated()), id: \.element.rawValue) { index, stage in
                        stageRow(stage, index: index)
                    }
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 8)
                .background(OrbitTheme.surface, in: RoundedRectangle(cornerRadius: 24))
            }
        }
    }

    private func stageRow(_ stage: ProjectStage, index: Int) -> some View {
        let isCurrent = index == currentStageIndex
        let isComplete = index < currentStageIndex

        return HStack(alignment: .top, spacing: 14) {
            VStack(spacing: 0) {
                Image(systemName: isComplete ? "checkmark.circle.fill" : stage.symbol)
                    .font(.body)
                    .foregroundStyle(isCurrent || isComplete ? tint : OrbitTheme.secondary)
                    .frame(width: 32, height: 32)
                    .background(isCurrent ? tint.opacity(0.12) : .clear, in: Circle())

                if index < ProjectStage.allCases.count - 1 {
                    Rectangle()
                        .fill(isComplete ? tint.opacity(0.45) : Color.white.opacity(0.09))
                        .frame(width: 1)
                        .frame(minHeight: 20)
                }
            }
            .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                Text(stage.title)
                    .font(.subheadline.weight(isCurrent ? .semibold : .regular))
                    .foregroundStyle(isCurrent || isComplete ? Color.white : OrbitTheme.secondary)

                Text(isCurrent ? "Текущий этап" : isComplete ? "Пройден" : "Впереди")
                    .font(.caption)
                    .foregroundStyle(isCurrent ? tint : OrbitTheme.secondary)
            }
            .padding(.top, 5)
            .padding(.bottom, 18)

            Spacer(minLength: 0)
        }
        .padding(.top, index == 0 ? 10 : 0)
        .accessibilityElement(children: .combine)
    }

    private var cloudTasksSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            sectionTitle("Облачные задачи", symbol: "cloud")
            Text("Статусы переданы Codex. Они не определяют процент готовности проекта или число агентов.")
                .font(.caption)
                .foregroundStyle(OrbitTheme.secondary)

            LazyVStack(alignment: .leading, spacing: 14) {
                ForEach(project.tasks) { task in
                    VStack(alignment: .leading, spacing: 12) {
                        Text(task.title)
                            .font(.headline)
                            .fixedSize(horizontal: false, vertical: true)
                        Label("Статус Codex: \(task.status)", systemImage: "tag")
                            .font(.caption)
                            .foregroundStyle(OrbitTheme.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        if let summary = task.summary, !summary.isEmpty {
                            Text(summary)
                                .font(.subheadline)
                                .foregroundStyle(.white.opacity(0.88))
                                .fixedSize(horizontal: false, vertical: true)
                                .textSelection(.enabled)
                        }
                        if let updatedAt = task.updatedAt {
                            Text(updatedAt, format: .dateTime.day().month(.abbreviated).hour().minute())
                                .font(.caption)
                                .foregroundStyle(OrbitTheme.secondary)
                        }
                        if let url = task.safeURL {
                            Link(destination: url) {
                                Label("Открыть в Codex", systemImage: "arrow.up.right.square")
                                    .font(.subheadline.weight(.medium))
                                    .frame(minHeight: 44)
                            }
                            .tint(tint)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(18)
                    .background(OrbitTheme.surface, in: RoundedRectangle(cornerRadius: 22))
                }
            }
        }
    }

    private var agentsSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            sectionTitle("Команда агентов", symbol: "person.2.fill")

            if let observedAt = project.briefUpdatedAt {
                (Text("Состояния на ") + Text(observedAt, format: .dateTime.day().month(.abbreviated).hour().minute()))
                    .font(.caption)
                    .foregroundStyle(OrbitTheme.secondary)
            }

            if project.agents.isEmpty {
                emptyState(hasBrief ? "В сводке нет записей об агентах" : project.isCloudSnapshot ? "Источник не сообщает состав агентов" : "Агенты пока не назначены",
                           symbol: "person.crop.circle.badge.questionmark")
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(project.agents.enumerated()), id: \.element.id) { index, agent in
                        VStack(alignment: .leading, spacing: 12) {
                            HStack(alignment: .top, spacing: 12) {
                                Image(systemName: agentSymbol(agent.status))
                                    .font(.body)
                                    .foregroundStyle(agentColor(agent.status))
                                    .frame(width: 40, height: 40)
                                    .background(agentColor(agent.status).opacity(0.10), in: RoundedRectangle(cornerRadius: 13))
                                    .accessibilityHidden(true)

                                VStack(alignment: .leading, spacing: 4) {
                                    Text(agent.name)
                                        .font(.subheadline.weight(.semibold))
                                    Text(agent.role)
                                        .font(.caption)
                                        .foregroundStyle(OrbitTheme.secondary)
                                }
                                Spacer(minLength: 0)
                            }

                            Label(agent.status.title, systemImage: statusSymbol(agent.status))
                                .font(.caption.weight(.medium))
                                .foregroundStyle(agentColor(agent.status))

                            Text(agent.currentTask)
                                .font(.subheadline)
                                .foregroundStyle(.white.opacity(0.86))
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 17)
                        .accessibilityElement(children: .combine)

                        if index < project.agents.count - 1 {
                            Divider().overlay(Color.white.opacity(0.04))
                        }
                    }
                }
                .padding(.horizontal, 18)
                .background(OrbitTheme.surface, in: RoundedRectangle(cornerRadius: 24))
            }
        }
    }

    private var reportsSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            sectionTitle("Последние отчёты", symbol: "doc.text")

            if project.reports.isEmpty {
                emptyState(hasBrief ? "В сводке нет отчётов" : project.isCloudSnapshot ? "Источник не передал отчёты" : "Отчётов пока нет", symbol: "doc")
            } else {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(project.reports.sorted { $0.createdAt > $1.createdAt }) { report in
                        DisclosureGroup(isExpanded: Binding(
                            get: { expandedReportIDs.contains(report.id) },
                            set: { isExpanded in
                                if isExpanded {
                                    expandedReportIDs.insert(report.id)
                                } else {
                                    expandedReportIDs.remove(report.id)
                                }
                            }
                        )) {
                            VStack(alignment: .leading, spacing: 14) {
                                Text(report.summary)
                                    .font(.subheadline)
                                    .foregroundStyle(.white.opacity(0.9))
                                    .textSelection(.enabled)

                                ForEach(Array(report.highlights.enumerated()), id: \.offset) { _, highlight in
                                    HStack(alignment: .top, spacing: 10) {
                                        Image(systemName: "checkmark")
                                            .font(.caption.weight(.semibold))
                                            .foregroundStyle(tint)
                                            .padding(.top, 3)
                                            .accessibilityHidden(true)
                                        Text(highlight)
                                            .font(.subheadline)
                                            .foregroundStyle(OrbitTheme.secondary)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.top, 16)
                            .padding(.bottom, 4)
                        } label: {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(report.createdAt, format: .dateTime.day().month(.abbreviated).hour().minute())
                                    .font(.caption)
                                    .foregroundStyle(OrbitTheme.secondary)
                                Text(report.title)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(.white)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            .padding(.vertical, 3)
                        }
                        .tint(tint)
                        .padding(18)
                        .background(OrbitTheme.surface, in: RoundedRectangle(cornerRadius: 22))
                    }
                }
            }
        }
    }

    private var stepsSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            sectionTitle("Что дальше", symbol: "arrow.up.forward")

            if project.steps.isEmpty {
                emptyState(hasBrief ? "В сводке нет следующих шагов" : project.isCloudSnapshot ? "Источник не передал следующие шаги" : "Следующие шаги ещё не добавлены",
                           symbol: "list.bullet")
            } else {
                VStack(alignment: .leading, spacing: 18) {
                    ForEach(project.steps) { step in
                        HStack(alignment: .top, spacing: 13) {
                            Image(systemName: step.isComplete ? "checkmark.circle.fill" : "circle")
                                .font(.title3)
                                .foregroundStyle(step.isComplete ? tint : OrbitTheme.secondary)
                                .padding(.top, 1)
                                .accessibilityHidden(true)

                            VStack(alignment: .leading, spacing: 5) {
                                Text(step.title)
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(step.isComplete ? OrbitTheme.secondary : .white)
                                    .strikethrough(step.isComplete, color: OrbitTheme.secondary.opacity(0.6))

                                Text(step.detail)
                                    .font(.caption)
                                    .foregroundStyle(OrbitTheme.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityElement(children: .combine)
                        .accessibilityValue(step.isComplete ? "Выполнено" : "Предстоит")
                    }
                }
                .padding(18)
                .background(OrbitTheme.surface, in: RoundedRectangle(cornerRadius: 24))
            }
        }
    }

    private func sectionTitle(_ title: String, symbol: String) -> some View {
        Label(title, systemImage: symbol)
            .font(.headline)
            .accessibilityAddTraits(.isHeader)
    }

    private func emptyState(_ title: String, symbol: String) -> some View {
        Label(title, systemImage: symbol)
            .font(.subheadline)
            .foregroundStyle(OrbitTheme.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(20)
            .background(OrbitTheme.surface, in: RoundedRectangle(cornerRadius: 22))
    }

    private func agentColor(_ status: AgentStatus) -> Color {
        switch status {
        case .working: return tint
        case .waiting: return Color(hex: "F2C97C")
        case .completed: return Color(hex: "99B6E8")
        }
    }

    private func agentSymbol(_ status: AgentStatus) -> String {
        switch status {
        case .working: return "cpu"
        case .waiting: return "hand.raised"
        case .completed: return "checkmark.seal"
        }
    }

    private func statusSymbol(_ status: AgentStatus) -> String {
        switch status {
        case .working: return "bolt.fill"
        case .waiting: return "pause.circle.fill"
        case .completed: return "checkmark.circle.fill"
        }
    }
}
