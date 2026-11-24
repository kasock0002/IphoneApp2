import Foundation

/// Illustrative, deterministic content. These are not real Codex projects or reports.
enum DemoData {
    static let snapshot: ProjectSnapshot = {
        let formatter = ISO8601DateFormatter()
        guard let generatedAt = formatter.date(from: "2026-11-15T10:30:00Z") else {
            preconditionFailure("The fixed demo timestamp must be a valid ISO 8601 date.")
        }

        func minutesAgo(_ minutes: Double) -> Date {
            generatedAt.addingTimeInterval(-minutes * 60)
        }

        let analytics = Project(
            id: "analytics",
            name: "Аналитика",
            subtitle: "От данных к решениям",
            symbol: "chart.xyaxis.line",
            tintHex: "A99AFF",
            stage: .development,
            progress: 0.6,
            updatedAt: minutesAgo(5),
            agents: [
                ProjectAgent(
                    id: "analytics-researcher", name: "Атлас", role: "Аналитик",
                    status: .working, currentTask: "Сопоставляет показатели по неделям и ищет отклонения."
                ),
                ProjectAgent(
                    id: "analytics-developer", name: "Нова", role: "Разработчик",
                    status: .working, currentTask: "Собирает графики и фильтры для сводной панели."
                ),
                ProjectAgent(
                    id: "analytics-reviewer", name: "Вега", role: "Проверка данных",
                    status: .completed, currentTask: "Проверила структуру набора данных и правила расчёта."
                )
            ],
            reports: [
                ProjectReport(
                    id: "analytics-report-2", title: "Первые выводы готовы",
                    summary: "Собрана структура аналитической панели. Следующий шаг — проверить выводы на контрольном наборе данных.",
                    createdAt: minutesAgo(12),
                    highlights: [
                        "Определены три ключевых показателя для главного экрана.",
                        "Подготовлено сравнение текущего и предыдущего периодов.",
                        "Нужно выбрать основной период: неделя или месяц."
                    ]
                ),
                ProjectReport(
                    id: "analytics-report-1", title: "Источники и метрики согласованы",
                    summary: "Сформирован пример входных данных и описаны правила расчёта метрик.",
                    createdAt: minutesAgo(95),
                    highlights: [
                        "Зафиксированы названия и единицы измерения показателей.",
                        "Добавлены проверки пропущенных и повторяющихся записей."
                    ]
                )
            ],
            steps: [
                ProjectStep(id: "analytics-step-1", title: "Описать задачу", detail: "Определить вопросы, на которые должна отвечать аналитика.", isComplete: true),
                ProjectStep(id: "analytics-step-2", title: "Подготовить данные", detail: "Проверить структуру, качество и правила расчётов.", isComplete: true),
                ProjectStep(id: "analytics-step-3", title: "Собрать основу панели", detail: "Разместить основные показатели и сравнение периодов.", isComplete: true),
                ProjectStep(id: "analytics-step-4", title: "Проверить выводы", detail: "Сверить графики и расчёты с контрольными примерами.", isComplete: false),
                ProjectStep(id: "analytics-step-5", title: "Подготовить итоговый обзор", detail: "Оформить выводы, ограничения и рекомендации.", isComplete: false)
            ]
        )

        let iphone = Project(
            id: "iphone-app",
            name: "Приложение iPhone",
            subtitle: "Центр управления проектами",
            symbol: "iphone",
            tintHex: "79E5DA",
            stage: .development,
            progress: 0.4,
            updatedAt: minutesAgo(8),
            agents: [
                ProjectAgent(
                    id: "iphone-designer", name: "Луна", role: "Дизайнер",
                    status: .completed, currentTask: "Подготовила структуру экранов и визуальный стиль."
                ),
                ProjectAgent(
                    id: "iphone-developer", name: "Орион", role: "iOS-разработчик",
                    status: .working, currentTask: "Добавляет выбор проекта в интерактивной 3D-сцене."
                )
            ],
            reports: [
                ProjectReport(
                    id: "iphone-report-1", title: "Навигация и визуальный стиль",
                    summary: "Определены главный экран с 3D-сценой и карточка проекта с этапами, агентами и отчётами.",
                    createdAt: minutesAgo(38),
                    highlights: [
                        "Главный экран объединяет все проекты в одной сцене.",
                        "Карточка проекта отвечает на вопросы: где мы, что сделано и что дальше.",
                        "Подключение реального источника данных ещё предстоит."
                    ]
                )
            ],
            steps: [
                ProjectStep(id: "iphone-step-1", title: "Определить сценарии", detail: "Выбрать данные, которые нужны для быстрого обзора работы.", isComplete: true),
                ProjectStep(id: "iphone-step-2", title: "Подготовить дизайн", detail: "Создать композицию главного экрана и карточки проекта.", isComplete: true),
                ProjectStep(id: "iphone-step-3", title: "Собрать 3D-сцену", detail: "Добавить анимацию агентов и нажатие на проекты.", isComplete: false),
                ProjectStep(id: "iphone-step-4", title: "Подключить источник данных", detail: "Согласовать доступный способ получения проектов и отчётов.", isComplete: false),
                ProjectStep(id: "iphone-step-5", title: "Проверить на iPhone", detail: "Проверить читаемость, управление и расход энергии на устройстве.", isComplete: false)
            ]
        )

        let reporting = Project(
            id: "reports",
            name: "Отчёты",
            subtitle: "Еженедельная картина прогресса",
            symbol: "doc.text.image",
            tintHex: "FFCA88",
            stage: .delivery,
            progress: 0.8,
            updatedAt: minutesAgo(22),
            agents: [
                ProjectAgent(
                    id: "reports-editor", name: "Спарк", role: "Редактор",
                    status: .waiting, currentTask: "Ждёт выбора структуры финального отчёта."
                ),
                ProjectAgent(
                    id: "reports-checker", name: "Эхо", role: "Проверка",
                    status: .completed, currentTask: "Сверила итоговые выводы с материалами примера."
                )
            ],
            reports: [
                ProjectReport(
                    id: "reports-report-1", title: "Черновик готов к просмотру",
                    summary: "Собран пример еженедельного отчёта. Для завершения нужно выбрать, какие разделы включить в итоговую версию.",
                    createdAt: minutesAgo(22),
                    highlights: [
                        "Есть краткая сводка и подробное описание выполненных шагов.",
                        "Открытые вопросы вынесены в отдельный список.",
                        "Требуется решение: краткий отчёт или полный обзор."
                    ]
                )
            ],
            steps: [
                ProjectStep(id: "reports-step-1", title: "Собрать материалы", detail: "Объединить результаты проектов за выбранный период.", isComplete: true),
                ProjectStep(id: "reports-step-2", title: "Выделить результаты", detail: "Описать сделанное и оставшиеся вопросы.", isComplete: true),
                ProjectStep(id: "reports-step-3", title: "Составить черновик", detail: "Собрать сводку, выводы и план следующего периода.", isComplete: true),
                ProjectStep(id: "reports-step-4", title: "Проверить содержание", detail: "Сверить выводы с исходными материалами.", isComplete: true),
                ProjectStep(id: "reports-step-5", title: "Утвердить итоговую версию", detail: "Выбрать состав разделов и завершить оформление отчёта.", isComplete: false)
            ]
        )

        return ProjectSnapshot(generatedAt: generatedAt, projects: [analytics, iphone, reporting])
    }()
}
