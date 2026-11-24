import Foundation
import XCTest
@testable import OrbitCore

final class ConnectionProfileTests: XCTestCase {
    private let token = "synthetic-profile-token-not-a-secret-123456789"

    func testValidProfileReturnsValuesForReview() throws {
        let connection = try ConnectionProfile.decode(document())

        XCTAssertEqual(connection.endpoint.absoluteString, "https://orbit.example/snapshot")
        XCTAssertEqual(connection.token, token)
    }

    func testFormatAndVersionMustMatch() throws {
        for changes: [String: Any] in [
            ["format": "project-snapshot"], ["format": "ORBIT-CONNECTION"],
            ["version": 0], ["version": 2], ["version": "1"],
            ["version": true], ["version": 1.5]
        ] {
            assertRejected(try document(changes: changes))
        }
    }

    func testAllFourFieldsAreRequiredWithoutAdditionalFields() throws {
        for missing in ["format", "version", "endpoint", "token"] {
            var values = profile()
            values.removeValue(forKey: missing)
            assertRejected(try JSONSerialization.data(withJSONObject: values))
        }
        assertRejected(try document(changes: ["password": "unexpected"]))
        assertRejected(try document(changes: ["projects": []]))
    }

    func testWrongShapesAndTypesAreRejected() throws {
        for data in [Data("null".utf8), Data("[]".utf8), Data("not-json".utf8)] {
            assertRejected(data)
        }
        for changes: [String: Any] in [
            ["endpoint": NSNull()], ["endpoint": ["host": "orbit.example"]],
            ["token": 123], ["token": NSNull()], ["format": true]
        ] {
            assertRejected(try document(changes: changes))
        }
    }

    func testProfileSizeLimitAppliesBeforeParsing() throws {
        let original = try document()
        var boundary = original
        boundary.append(Data(repeating: 32, count: ConnectionProfile.maximumBytes - original.count))
        XCTAssertEqual(boundary.count, ConnectionProfile.maximumBytes)
        XCTAssertNoThrow(try ConnectionProfile.decode(boundary))
        boundary.append(32)
        XCTAssertThrowsError(try ConnectionProfile.decode(boundary)) { error in
            guard let connectionError = error as? ConnectionError,
                  case .profileTooLarge = connectionError else {
                return XCTFail("Expected size rejection")
            }
        }
    }

    func testOnlyDirectHTTPSAddressesAreAccepted() throws {
        for address in [
            "http://orbit.example/snapshot", "file:///snapshot", "/snapshot", "https:///snapshot",
            "https://user@orbit.example/snapshot", "https://user:password@orbit.example/snapshot",
            "https://@orbit.example/snapshot", "https://orbit.example/snapshot?token=secret",
            "https://orbit.example/snapshot?", "https://orbit.example/snapshot#section",
            "https://orbit.example/snapshot#"
        ] {
            assertRejected(try document(changes: ["endpoint": address]))
        }
    }

    func testWhitespaceAndControlsCannotBeSilentlyEncodedIntoAddresses() throws {
        for address in [
            " https://orbit.example/snapshot", "https://orbit.example/snapshot ",
            "https://orbit.example/snap shot", "https://orbit.example/snapshot\n",
            "https://orbit.example/snap\tshot", "https://orbit.example/\u{0000}snapshot",
            "https://orbit.example/\u{007F}snapshot", "https://orbit.example/snap\u{00A0}shot",
            "https://exam%20ple.example/snapshot"
        ] {
            assertRejected(try document(changes: ["endpoint": address]))
        }
    }

    func testPortsMustBeValidIfSpecified() throws {
        for address in [
            "https://orbit.example:0/snapshot", "https://orbit.example:65536/snapshot",
            "https://orbit.example:-1/snapshot", "https://orbit.example:abc/snapshot"
        ] {
            assertRejected(try document(changes: ["endpoint": address]))
        }
        for address in ["https://orbit.example:443/snapshot", "https://orbit.example:8443/snapshot"] {
            XCTAssertEqual(try ConnectionProfile.decode(document(changes: ["endpoint": address])).endpoint.absoluteString, address)
        }
    }

    func testTokenBoundsAndPrintableASCII() throws {
        for value in [String(repeating: "x", count: 31), String(repeating: "x", count: 513),
                      token + " ", token + "\n", token + "\u{007F}", token + "ё"] {
            assertRejected(try document(changes: ["token": value]))
        }
        for length in [32, 512] {
            let value = String(repeating: "x", count: length)
            XCTAssertEqual(try ConnectionProfile.decode(document(changes: ["token": value])).token, value)
        }
    }

    func testSharedConnectionValidatorAlsoRejectsUnsafeManualAddresses() throws {
        XCTAssertThrowsError(try SnapshotConnection.validated(endpoint: "https://orbit.example/snapshot\n", token: token))
        XCTAssertThrowsError(try SnapshotConnection.validated(endpoint: "https://orbit.example:0/snapshot", token: token))
        XCTAssertNoThrow(try SnapshotConnection.validated(endpoint: "https://orbit.example/snapshot", token: token))
    }

    func testErrorsDoNotExposeInputSecretsOrPrivateAddresses() throws {
        let address = "http://private-profile-host.invalid/snapshot?secret=PRIVATE_VALUE"
        let secret = "PRIVATE_READ_TOKEN_123456789012345678901234567890"
        for data in [
            try document(changes: ["endpoint": address, "token": secret]),
            try document(changes: ["token": secret + "\n"]),
            Data("{\"PRIVATE_BROKEN_JSON\":\"\(secret)\"".utf8)
        ] {
            XCTAssertThrowsError(try ConnectionProfile.decode(data)) { error in
                let message = error.localizedDescription
                for privateValue in [address, "private-profile-host", "PRIVATE_VALUE", secret, "PRIVATE_BROKEN_JSON"] {
                    XCTAssertFalse(message.contains(privateValue), message)
                }
            }
        }
    }

    private func profile(changes: [String: Any] = [:]) -> [String: Any] {
        var value: [String: Any] = [
            "format": "orbit-connection", "version": 1,
            "endpoint": "https://orbit.example/snapshot", "token": token
        ]
        value.merge(changes) { _, supplied in supplied }
        return value
    }

    private func document(changes: [String: Any] = [:]) throws -> Data {
        try JSONSerialization.data(withJSONObject: profile(changes: changes))
    }

    private func assertRejected(_ data: Data, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try ConnectionProfile.decode(data), file: file, line: line) { error in
            XCTAssertTrue(error is ConnectionError, file: file, line: line)
        }
    }
}
