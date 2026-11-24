import SwiftUI
import UniformTypeIdentifiers

struct DataSourceView: View {
    @EnvironmentObject private var store: SnapshotStore
    @Environment(\.dismiss) private var dismiss
    @State private var endpoint = ""
    @State private var token = ""
    @State private var localError: String?
    @State private var showsConnectionImporter = false
    @State private var profileNotice: String?
    let onImport: () -> Void
    let onDemo: () -> Void

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Image(systemName: "externaldrive.badge.icloud")
                        .font(.largeTitle)
                        .foregroundStyle(OrbitTheme.accent)
                    Text("Твои проекты,\nодна картина.")
                        .font(.largeTitle.bold())
                    Text("Текущий источник: \(store.sourceTitle)")
                        .font(.headline)

                    connectionForm

                    Divider()
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Снимок из файла").font(.headline)
                        Text("Импортируйте JSON в формате Orbit из «Файлов». Импорт приостановит автоматическое обновление.")
                            .foregroundStyle(OrbitTheme.secondary)
                        Button(action: onImport) {
                            Label("Импортировать JSON-снимок", systemImage: "square.and.arrow.down")
                                .frame(maxWidth: .infinity, minHeight: 48)
                        }
                        .buttonStyle(.bordered)
                        exampleLinks
                    }

                    Button("Показать демонстрационные проекты", action: onDemo)
                        .frame(minHeight: 44)
                    Text("Демонстрационный режим также приостановит синхронизацию. Чтобы удалить сохранённые настройки и токен, отключите сервис выше.")
                        .font(.footnote)
                        .foregroundStyle(OrbitTheme.secondary)
                    Text("Снимок хранится на iPhone. Анимация отражает состояние на момент снимка. Автоматические обновления выполняются только при открытом приложении.")
                        .font(.footnote)
                        .foregroundStyle(OrbitTheme.secondary)
                }
                .padding(24)
            }
            .background(OrbitTheme.background)
            .navigationTitle("Источник данных")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Готово") { dismiss() }.frame(minHeight: 44)
                }
            }
            .onAppear { if endpoint.isEmpty { endpoint = store.connectionDisplayURL } }
            .fileImporter(isPresented: $showsConnectionImporter, allowedContentTypes: [.json]) { result in
                profileNotice = nil
                switch result {
                case .success(let url): importConnection(from: url)
                case .failure(let error):
                    let cancelled = (error as NSError).domain == NSCocoaErrorDomain
                        && (error as NSError).code == CocoaError.Code.userCancelled.rawValue
                    if !cancelled { localError = "Не удалось открыть файл подключения. Попробуйте выбрать его ещё раз." }
                }
            }
            .alert("Подключение", isPresented: Binding(
                get: { localError != nil }, set: { if !$0 { localError = nil } }
            )) { Button("Понятно", role: .cancel) { localError = nil } }
            message: { Text(localError ?? "") }
        }
        .tint(OrbitTheme.accent)
    }

    private var connectionForm: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Облачный Codex").font(.title2.bold())
            Text("Подключите сервис Orbit, запущенный на компьютере с авторизованным Codex CLI. Задачи группируются по окружениям Codex. Раздел «Проекты» и чаты ChatGPT этим способом не читаются.")
                .font(.subheadline)
                .foregroundStyle(OrbitTheme.secondary)

            Button { showsConnectionImporter = true } label: {
                Label("Импортировать подключение", systemImage: "square.and.arrow.down")
                    .frame(minHeight: 44)
            }
            if let profileNotice {
                Text(profileNotice)
                    .font(.footnote)
                    .foregroundStyle(OrbitTheme.secondary)
            }

            VStack(alignment: .leading, spacing: 7) {
                Text("HTTPS-адрес снимка").font(.subheadline.weight(.medium))
                TextField("https://your-domain.example/snapshot", text: $endpoint)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel("HTTPS-адрес сервиса Orbit")
            }
            VStack(alignment: .leading, spacing: 7) {
                Text("Токен сервиса Orbit").font(.subheadline.weight(.medium))
                SecureField(store.hasStoredConnection ? "Сохранён; можно оставить пустым" : "Токен из настроек сервиса", text: $token)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(nil)
                    .textFieldStyle(.roundedBorder)
                    .privacySensitive()
                Text("Это отдельный токен Orbit, не пароль и не токен ChatGPT. Он хранится в защищённой связке ключей iPhone.")
                    .font(.footnote)
                    .foregroundStyle(OrbitTheme.secondary)
            }

            Button {
                if store.saveConnection(endpoint: endpoint, token: token) {
                    token = ""
                    dismiss()
                } else { captureError() }
            } label: {
                Label(store.hasConnection ? "Сохранить и обновить" : "Подключить и обновить", systemImage: "arrow.triangle.2.circlepath")
                    .font(.headline)
                    .frame(maxWidth: .infinity, minHeight: 48)
            }
            .buttonStyle(.borderedProminent)
            .tint(OrbitTheme.accent)
            .foregroundStyle(OrbitTheme.background)

            if store.hasStoredConnection {
                Label(store.hasConnection ? "Автообновление включено" : "Подключение сохранено, обновление приостановлено",
                      systemImage: store.hasConnection ? "checkmark.circle" : "pause.circle")
                    .font(.footnote)
                    .foregroundStyle(OrbitTheme.secondary)
                Button("Отключить сервис и удалить токен", role: .destructive) {
                    store.disconnect()
                    captureError()
                    if !store.hasStoredConnection { endpoint = ""; token = "" }
                }
                .frame(minHeight: 44)
            }

            if let notice = store.connectionNotice {
                Text(notice).font(.footnote).foregroundStyle(OrbitTheme.secondary)
                    .accessibilityAddTraits(.updatesFrequently)
            }
        }
    }

    private var exampleLinks: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let example = Bundle.main.url(forResource: "example-snapshot", withExtension: "json") {
                ShareLink(item: example) {
                    Label("Пример проекта с отчётами", systemImage: "doc.text").frame(minHeight: 44)
                }
            }
            if let example = Bundle.main.url(forResource: "example-cloud-snapshot", withExtension: "json") {
                ShareLink(item: example) {
                    Label("Пример облачных задач", systemImage: "cloud").frame(minHeight: 44)
                }
            }
            if let example = Bundle.main.url(forResource: "example-enriched-snapshot", withExtension: "json") {
                ShareLink(item: example) {
                    Label("Пример задач со сводкой агентов", systemImage: "person.2").frame(minHeight: 44)
                }
            }
        }
    }

    private func captureError() {
        if let message = store.errorMessage {
            localError = message
            store.errorMessage = nil
        }
    }

    private func importConnection(from url: URL) {
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        do {
            let file = try FileHandle(forReadingFrom: url)
            defer { try? file.close() }
            let data = try file.read(upToCount: ConnectionProfile.maximumBytes + 1) ?? Data()
            let connection = try ConnectionProfile.decode(data)
            endpoint = connection.endpoint.absoluteString
            token = connection.token
            profileNotice = "Подключение загружено. Проверьте адрес сервиса и подтвердите подключение кнопкой ниже."
        } catch let error as ConnectionError {
            localError = error.localizedDescription
        } catch {
            localError = "Не удалось прочитать файл подключения. Проверьте, что он доступен в «Файлах»."
        }
    }
}
