import Foundation
import Security

enum ConnectionKeychain {
    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: (Bundle.main.bundleIdentifier ?? "app.orbit.projectdesk") + ".bridge",
         kSecAttrAccount as String: "snapshot-connection"]
    }

    static func load() throws -> SnapshotConnection? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw ConnectionError.keychain }
        let connection = try JSONDecoder().decode(SnapshotConnection.self, from: data)
        return try SnapshotConnection.validated(endpoint: connection.endpoint.absoluteString, token: connection.token)
    }

    static func save(_ connection: SnapshotConnection) throws {
        let changes: [String: Any] = [
            kSecValueData as String: try JSONEncoder().encode(connection),
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        ]
        let status = SecItemUpdate(query as CFDictionary, changes as CFDictionary)
        if status == errSecItemNotFound {
            var newItem = query
            changes.forEach { newItem[$0.key] = $0.value }
            guard SecItemAdd(newItem as CFDictionary, nil) == errSecSuccess else { throw ConnectionError.keychain }
        } else if status != errSecSuccess { throw ConnectionError.keychain }
    }

    static func delete() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw ConnectionError.keychain }
    }
}

struct RemoteSnapshot {
    let data: Data
    let isStale: Bool
}

enum SnapshotClient {
    private static let redirectBlocker = SnapshotRedirectBlocker()
    private static let session: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = 30
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration, delegate: redirectBlocker, delegateQueue: nil)
    }()

    static func fetch(_ connection: SnapshotConnection) async throws -> RemoteSnapshot {
        var request = URLRequest(url: connection.endpoint)
        request.setValue("Bearer " + connection.token, forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse else { throw ConnectionError.invalidResponse }
        guard http.statusCode == 200 else {
            switch http.statusCode {
            case 401, 403: throw ConnectionError.unauthorized
            case 300..<400: throw ConnectionError.redirect
            case 503: throw ConnectionError.notReady
            default: throw ConnectionError.http(http.statusCode)
            }
        }
        guard http.mimeType?.lowercased() == "application/json" else { throw ConnectionError.invalidResponse }
        guard response.expectedContentLength <= Int64(SnapshotCodec.maximumBytes) else { throw SnapshotError.tooLarge }
        var data = Data()
        for try await byte in bytes {
            try Task.checkCancellation()
            guard data.count < SnapshotCodec.maximumBytes else { throw SnapshotError.tooLarge }
            data.append(byte)
        }
        return RemoteSnapshot(data: data,
                              isStale: http.value(forHTTPHeaderField: "X-Orbit-Stale")?.lowercased() == "true")
    }
}

/// Do not forward bridge credentials to a redirected host or path.
private final class SnapshotRedirectBlocker: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
