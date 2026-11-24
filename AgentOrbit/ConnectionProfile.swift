import Foundation

struct SnapshotConnection: Codable {
    let endpoint: URL
    let token: String

    static func validated(endpoint: String, token: String) throws -> SnapshotConnection {
        let forbidden = CharacterSet.whitespacesAndNewlines.union(.controlCharacters)
        guard !endpoint.unicodeScalars.contains(where: { forbidden.contains($0) }),
              let components = URLComponents(string: endpoint),
              components.scheme?.lowercased() == "https",
              let host = components.host, !host.isEmpty,
              !host.unicodeScalars.contains(where: { forbidden.contains($0) }),
              components.user == nil, components.password == nil,
              components.fragment == nil, components.query == nil,
              components.port.map({ (1...65535).contains($0) }) ?? true,
              let url = components.url else {
            throw ConnectionError.invalidAddress
        }
        guard (32...512).contains(token.utf8.count),
              token.utf8.allSatisfy({ (33...126).contains($0) }) else {
            throw ConnectionError.invalidToken
        }
        return SnapshotConnection(endpoint: url, token: token)
    }
}

/// A private setup document. Decoding only fills a form; it never connects to a service.
enum ConnectionProfile {
    static let maximumBytes = 16 * 1024

    static func decode(_ data: Data) throws -> SnapshotConnection {
        guard data.count <= maximumBytes else { throw ConnectionError.profileTooLarge }
        let profile: Envelope
        do { profile = try JSONDecoder().decode(Envelope.self, from: data) }
        catch { throw ConnectionError.invalidProfile }
        guard profile.format == "orbit-connection", profile.version == 1 else {
            throw ConnectionError.invalidProfile
        }
        return try SnapshotConnection.validated(endpoint: profile.endpoint, token: profile.token)
    }

    private struct Envelope: Decodable {
        let format: String
        let version: Int
        let endpoint: String
        let token: String

        init(from decoder: Decoder) throws {
            let values = try decoder.container(keyedBy: Key.self)
            guard Set(values.allKeys.map(\.stringValue)) == Set(["format", "version", "endpoint", "token"]) else {
                throw ConnectionError.invalidProfile
            }
            format = try values.decode(String.self, forKey: Key("format"))
            version = try values.decode(Int.self, forKey: Key("version"))
            endpoint = try values.decode(String.self, forKey: Key("endpoint"))
            token = try values.decode(String.self, forKey: Key("token"))
        }
    }

    private struct Key: CodingKey {
        let stringValue: String
        var intValue: Int? { nil }
        init(_ string: String) { stringValue = string }
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { return nil }
    }
}

enum ConnectionError: LocalizedError {
    case invalidAddress, invalidToken, keychain, invalidResponse, unauthorized, redirect, notReady
    case invalidProfile, profileTooLarge
    case http(Int)

    var errorDescription: String? {
        switch self {
        case .invalidAddress: return "Укажите прямой HTTPS-адрес снимка, без пробелов, логина, пароля, параметров и фрагмента. Например: https://your-domain.example/snapshot."
        case .invalidToken: return "Токен сервиса должен содержать от 32 до 512 печатных ASCII-символов без пробелов. Это токен Orbit, а не пароль ChatGPT."
        case .keychain: return "Не удалось получить доступ к защищённому хранилищу подключения."
        case .invalidResponse: return "Сервис должен вернуть JSON-снимок Orbit."
        case .unauthorized: return "Сервис отклонил токен. Проверьте токен Orbit в настройках подключения."
        case .redirect: return "Сервис перенаправляет запрос. Укажите конечный HTTPS-адрес снимка."
        case .notReady: return "Сервис ещё не получил снимок Codex. Проверьте авторизацию CLI на компьютере с сервисом."
        case .invalidProfile: return "Нужен файл подключения Orbit версии 1 с адресом сервиса и токеном."
        case .profileTooLarge: return "Максимальный размер файла подключения — 16 КиБ."
        case .http(let status): return "Сервис вернул ошибку HTTP \(status). Предыдущие данные сохранены."
        }
    }
}
