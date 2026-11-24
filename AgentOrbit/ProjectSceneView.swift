import SceneKit
import SwiftUI
import UIKit
import simd

/// A local visualization of the current snapshot. Motion never implies a live connection.
struct ProjectSceneView: UIViewRepresentable {
    let projects: [Project]
    let selectedProjectID: String?
    let reduceMotion: Bool
    let onSelect: (String) -> Void

    @Environment(\.scenePhase) private var scenePhase

    func makeCoordinator() -> Coordinator { Coordinator(onSelect: onSelect) }

    func makeUIView(context: Context) -> SCNView {
        let view = OrbitSceneView(frame: .zero)
        view.backgroundColor = UIColor(orbitHex: "0A0F1B")
        view.antialiasingMode = .multisampling4X
        view.preferredFramesPerSecond = 30
        view.allowsCameraControl = false
        view.autoenablesDefaultLighting = false
        view.accessibilityElementsHidden = true // Equivalent native project buttons accompany the scene.
        view.addGestureRecognizer(UITapGestureRecognizer(target: context.coordinator,
                                                       action: #selector(Coordinator.selectProject(_:))))
        updateUIView(view, context: context)
        return view
    }

    func updateUIView(_ uiView: SCNView, context: Context) {
        guard let view = uiView as? OrbitSceneView else { return }
        context.coordinator.onSelect = onSelect
        context.coordinator.update(view: view, projects: projects,
                                   selectedProjectID: selectedProjectID, reduceMotion: reduceMotion)
        view.motionEnabled = !reduceMotion
        view.environmentIsActive = scenePhase == .active
        view.updatePlayback()
    }

    static func dismantleUIView(_ uiView: SCNView, coordinator: Coordinator) {
        uiView.isPlaying = false
        uiView.scene?.isPaused = true
        uiView.scene = nil
    }

    final class Coordinator: NSObject {
        var onSelect: (String) -> Void
        private var contentSignature = ""
        private var selectedID: String?
        private var selectionRings: [String: SCNNode] = [:]
        private var projectColors: [String: UIColor] = [:]

        init(onSelect: @escaping (String) -> Void) { self.onSelect = onSelect }

        fileprivate func update(view: OrbitSceneView, projects: [Project], selectedProjectID: String?, reduceMotion: Bool) {
            let signature = projects.map { project in
                let agents = project.agents.map { "\($0.id):\($0.status.rawValue)" }.joined(separator: ",")
                let progress = project.progress.map { String($0) } ?? "unknown"
                let stage = project.stage?.rawValue ?? "unknown"
                let source = project.source ?? "snapshot"
                return "\(project.id)|\(project.name)|\(project.tintHex)|\(progress)|\(stage)|\(source)|tasks:\(project.tasks.count)|\(agents)"
            }.joined(separator: ";") + "|motion:\(!reduceMotion)"
            if signature != contentSignature || view.scene == nil {
                contentSignature = signature
                buildScene(in: view, projects: projects, reduceMotion: reduceMotion)
                selectedID = nil
            }
            guard selectedID != selectedProjectID else { return }
            selectedID = selectedProjectID
            SCNTransaction.begin()
            SCNTransaction.animationDuration = reduceMotion ? 0 : 0.2
            for (id, ring) in selectionRings {
                let active = id == selectedProjectID
                ring.opacity = active ? 1 : 0.33
                ring.scale = SCNVector3(active ? 1.1 : 1, 1, active ? 1.1 : 1)
                ring.geometry?.firstMaterial?.emission.contents = projectColors[id]?.withAlphaComponent(active ? 0.9 : 0.15)
            }
            SCNTransaction.commit()
        }

        @objc func selectProject(_ recognizer: UITapGestureRecognizer) {
            guard let view = recognizer.view as? SCNView else { return }
            let hits = view.hitTest(recognizer.location(in: view), options: nil)
            for hit in hits {
                var node: SCNNode? = hit.node
                while let current = node {
                    if let name = current.name, name.hasPrefix("project:") {
                        onSelect(String(name.dropFirst("project:".count)))
                        return
                    }
                    node = current.parent
                }
            }
        }

        private func buildScene(in view: OrbitSceneView, projects: [Project], reduceMotion: Bool) {
            selectionRings.removeAll()
            projectColors.removeAll()
            let scene = SCNScene()
            scene.background.contents = UIColor(orbitHex: "0A0F1B")

            let camera = SCNNode()
            camera.camera = SCNCamera()
            camera.camera?.usesOrthographicProjection = true
            camera.camera?.zNear = 0.1
            camera.camera?.zFar = 100
            camera.position = SCNVector3(0, 10, 13)
            camera.look(at: SCNVector3(0, 0, 0))
            scene.rootNode.addChildNode(camera)

            let ambient = SCNNode()
            ambient.light = SCNLight()
            ambient.light?.type = .ambient
            ambient.light?.color = UIColor(orbitHex: "7E9BB8")
            ambient.light?.intensity = 650
            scene.rootNode.addChildNode(ambient)

            let key = SCNNode()
            key.light = SCNLight()
            key.light?.type = .omni
            key.light?.color = UIColor(orbitHex: "BCDFFF")
            key.light?.intensity = 1100
            key.position = SCNVector3(-4, 7, 5)
            scene.rootNode.addChildNode(key)

            let count = projects.count
            let radius = max(3.1, Float(count) * 0.57)
            let hubPosition = SCNVector3(0, 0, count < 3 ? -1.5 : -0.2)
            addHub(to: scene.rootNode, position: hubPosition, reduceMotion: reduceMotion)
            addStarField(to: scene.rootNode, radius: max(5, radius + 2))

            for (index, project) in projects.enumerated() {
                let position: SCNVector3
                if count == 1 {
                    position = SCNVector3(0, 0, 1.25)
                } else if count == 2 {
                    position = SCNVector3(index == 0 ? -2.2 : 2.2, 0, 0.7)
                } else if count == 3 {
                    position = [SCNVector3(-2.7, 0, -1.65), SCNVector3(2.7, 0, -1.65), SCNVector3(0, 0, 2.3)][index]
                } else {
                    let angle = Float(index) / Float(count) * .pi * 2 + .pi
                    position = SCNVector3(sin(angle) * radius, 0, cos(angle) * radius)
                }
                let start = SCNVector3(hubPosition.x, 0.08, hubPosition.z)
                let end = SCNVector3(position.x, 0.08, position.z)
                addLine(from: start, to: end, color: UIColor(orbitHex: "263F57"), parent: scene.rootNode)
                addProject(project, at: position, to: scene.rootNode, reduceMotion: reduceMotion)
            }

            view.scene = scene
            view.pointOfView = camera
            view.framingDiameter = count > 3 ? Double(radius * 2 + 3.4) : 9.3
            view.setNeedsLayout()
        }

        private func addHub(to root: SCNNode, position: SCNVector3, reduceMotion: Bool) {
            let hub = SCNNode()
            hub.position = position
            root.addChildNode(hub)
            let sphere = SCNNode(geometry: SCNSphere(radius: 0.29))
            sphere.position.y = 0.35
            sphere.geometry?.materials = [material(UIColor(orbitHex: "B5E8FF"), glow: 0.4)]
            hub.addChildNode(sphere)
            let ring = SCNNode(geometry: SCNTorus(ringRadius: 0.52, pipeRadius: 0.018))
            ring.position.y = 0.36
            ring.eulerAngles.x = 0.42
            ring.geometry?.materials = [material(UIColor(orbitHex: "6397B8"), glow: 0.5)]
            hub.addChildNode(ring)
            if !reduceMotion {
                ring.runAction(.repeatForever(.rotateBy(x: 0, y: .pi * 2, z: 0, duration: 28)))
            }
        }

        private func addProject(_ project: Project, at position: SCNVector3, to sceneRoot: SCNNode, reduceMotion: Bool) {
            let root = SCNNode()
            root.name = "project:\(project.id)"
            root.position = position
            sceneRoot.addChildNode(root)
            let tint = UIColor(orbitHex: project.tintHex)
            projectColors[project.id] = tint

            let platform = SCNNode(geometry: SCNCylinder(radius: 0.83, height: 0.16))
            platform.geometry?.materials = [material(UIColor(orbitHex: "182B40"), glow: 0)]
            root.addChildNode(platform)

            let selection = SCNNode(geometry: SCNTorus(ringRadius: 0.86, pipeRadius: 0.025))
            selection.position.y = 0.1
            selection.geometry?.materials = [material(tint, glow: 0.45)]
            selection.opacity = 0.33
            root.addChildNode(selection)
            selectionRings[project.id] = selection

            let core = SCNNode(geometry: SCNBox(width: 0.48, height: 0.56, length: 0.48, chamferRadius: 0.09))
            core.position.y = 0.4
            core.geometry?.materials = [material(tint.withAlphaComponent(0.9), glow: 0.2)]
            root.addChildNode(core)

            // The thin outer orbit remains static. Agent bodies move only for working agents.
            let orbit = SCNNode(geometry: SCNTorus(ringRadius: 1.14, pipeRadius: 0.008))
            orbit.position.y = 0.24
            orbit.geometry?.materials = [material(UIColor(orbitHex: "3B5269"), glow: 0)]
            root.addChildNode(orbit)

            let visibleAgents = Array(project.agents.prefix(8))
            for (index, agent) in visibleAgents.enumerated() {
                let angle = Double(index) / Double(max(visibleAgents.count, 1)) * .pi * 2
                let pivot = SCNNode()
                pivot.eulerAngles.y = Float(angle)
                root.addChildNode(pivot)
                let robot = makeRobot(status: agent.status, tint: tint)
                robot.position = SCNVector3(1.14, 0.4, 0)
                pivot.addChildNode(robot)
                if agent.status == .working && !reduceMotion {
                    pivot.runAction(.repeatForever(.rotateBy(x: 0, y: .pi * 2, z: 0, duration: 18 + Double(index) * 3)))
                    robot.runAction(.repeatForever(.sequence([
                        .moveBy(x: 0, y: 0.07, z: 0, duration: 0.85),
                        .moveBy(x: 0, y: -0.07, z: 0, duration: 0.85)
                    ])))
                }
            }

            addLabel(project.name, position: SCNVector3(0, 0.18, 1.62), size: 0.26,
                     color: UIColor(orbitHex: "E2ECF7"), parent: root)
            let summary: String
            if project.isCloudSnapshot {
                // A cloud task is not evidence of a separate agent or a project's readiness.
                summary = "Задач: \(project.tasks.count) · \(project.stage?.title ?? "этап не указан")"
            } else {
                let working = project.agents.filter { $0.status == .working }.count
                let progressLabel: String
                if let progress = project.progress, progress.isFinite {
                    let percent = Int((min(max(progress, 0), 1) * 100).rounded())
                    progressLabel = "\(percent)%"
                } else {
                    progressLabel = "прогресс не указан"
                }
                summary = "\(working) в работе · \(progressLabel)"
            }
            addLabel(summary, position: SCNVector3(0, -0.1, 1.96), size: 0.18,
                     color: UIColor(orbitHex: "9BAEC4"), parent: root)
        }

        private func makeRobot(status: AgentStatus, tint: UIColor) -> SCNNode {
            let robot = SCNNode()
            let color: UIColor
            switch status {
            case .working: color = tint
            case .waiting: color = UIColor(orbitHex: "F4BC69")
            case .completed: color = UIColor(orbitHex: "78D6AE")
            }
            let body = SCNNode(geometry: SCNBox(width: 0.22, height: 0.23, length: 0.18, chamferRadius: 0.045))
            body.geometry?.materials = [material(color, glow: 0.18)]
            robot.addChildNode(body)
            for x in [Float(-0.047), Float(0.047)] {
                let eye = SCNNode(geometry: SCNSphere(radius: 0.021))
                eye.position = SCNVector3(x, 0.025, 0.095)
                eye.geometry?.materials = [material(UIColor(orbitHex: "E8F7FF"), glow: 0.8)]
                body.addChildNode(eye)
            }
            let antenna = SCNNode(geometry: SCNCylinder(radius: 0.009, height: 0.07))
            antenna.position.y = 0.145
            antenna.geometry?.materials = [material(color, glow: 0.1)]
            body.addChildNode(antenna)
            let tip = SCNNode(geometry: SCNSphere(radius: 0.024))
            tip.position.y = 0.19
            tip.geometry?.materials = [material(color, glow: 0.7)]
            body.addChildNode(tip)
            if status == .waiting {
                for x in [Float(-0.045), Float(0.045)] {
                    let pause = SCNNode(geometry: SCNBox(width: 0.025, height: 0.09, length: 0.025, chamferRadius: 0.005))
                    pause.position = SCNVector3(x, 0.32, 0)
                    pause.geometry?.materials = [material(color, glow: 0.4)]
                    robot.addChildNode(pause)
                }
            } else if status == .completed {
                addLabel("✓", position: SCNVector3(0, 0.26, 0), size: 0.18, color: color, parent: robot)
            }
            return robot
        }

        private func addLabel(_ value: String, position: SCNVector3, size: Float, color: UIColor, parent: SCNNode) {
            let text = SCNText(string: value, extrusionDepth: 0)
            text.font = .systemFont(ofSize: 1, weight: .medium)
            text.flatness = 0.2
            let ink = material(color, glow: 1)
            ink.lightingModel = .constant
            ink.isDoubleSided = true
            text.materials = [ink]
            let node = SCNNode(geometry: text)
            let bounds = node.boundingBox
            node.pivot = SCNMatrix4MakeTranslation((bounds.min.x + bounds.max.x) / 2, bounds.min.y, 0)
            let width = max(bounds.max.x - bounds.min.x, 0.01)
            let scale = min(size, 3.3 / width)
            node.scale = SCNVector3(scale, scale, scale)
            node.position = position
            let billboard = SCNBillboardConstraint()
            billboard.freeAxes = .all
            node.constraints = [billboard]
            parent.addChildNode(node)
        }

        private func addLine(from start: SCNVector3, to end: SCNVector3, color: UIColor, parent: SCNNode) {
            let delta = SIMD3<Float>(end.x - start.x, end.y - start.y, end.z - start.z)
            let length = simd_length(delta)
            guard length > 0 else { return }
            let line = SCNNode(geometry: SCNCylinder(radius: 0.009, height: CGFloat(length)))
            line.position = SCNVector3((start.x + end.x) / 2, (start.y + end.y) / 2, (start.z + end.z) / 2)
            line.simdOrientation = simd_quatf(from: SIMD3<Float>(0, 1, 0), to: delta / length)
            line.geometry?.materials = [material(color, glow: 0.1)]
            parent.addChildNode(line)
        }

        private func addStarField(to root: SCNNode, radius: Float) {
            // Fixed geometry avoids random changes when snapshots update.
            for index in 0..<48 {
                let angle = Float(index) * 2.399963
                let distance = radius * sqrt(Float(index + 1) / 48)
                let dot = SCNNode(geometry: SCNSphere(radius: index % 5 == 0 ? 0.018 : 0.009))
                dot.position = SCNVector3(cos(angle) * distance, -0.24, sin(angle) * distance)
                dot.geometry?.materials = [material(UIColor(orbitHex: "2D455E"), glow: 0.3)]
                root.addChildNode(dot)
            }
        }

        private func material(_ color: UIColor, glow: CGFloat) -> SCNMaterial {
            let material = SCNMaterial()
            material.lightingModel = .physicallyBased
            material.diffuse.contents = color
            material.emission.contents = color.withAlphaComponent(glow)
            material.roughness.contents = 0.4
            material.metalness.contents = 0.22
            return material
        }
    }
}

/// Stops rendering when detached or inactive, and frames any supported project count.
private final class OrbitSceneView: SCNView {
    var motionEnabled = true
    var environmentIsActive = true
    var framingDiameter: Double = 9.3

    override init(frame: CGRect, options: [String: Any]? = nil) {
        super.init(frame: frame, options: options)
        NotificationCenter.default.addObserver(self, selector: #selector(applicationStateChanged(_:)),
                                               name: UIApplication.willResignActiveNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(applicationStateChanged(_:)),
                                               name: UIApplication.didBecomeActiveNotification, object: nil)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    deinit { NotificationCenter.default.removeObserver(self) }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        updatePlayback()
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        guard bounds.width > 0, bounds.height > 0 else { return }
        let aspect = Double(bounds.width / bounds.height)
        pointOfView?.camera?.orthographicScale = max(framingDiameter * 0.78, framingDiameter / aspect)
    }

    @objc private func applicationStateChanged(_ notification: Notification) {
        if notification.name == UIApplication.willResignActiveNotification {
            scene?.isPaused = true
            isPlaying = false
            rendersContinuously = false
        } else {
            updatePlayback()
        }
    }

    func updatePlayback() {
        let active = environmentIsActive && window != nil && UIApplication.shared.applicationState == .active
        let animate = active && motionEnabled
        scene?.isPaused = !animate
        isPlaying = animate
        rendersContinuously = animate
        if !animate { setNeedsDisplay() }
    }
}

private extension UIColor {
    convenience init(orbitHex: String) {
        let raw = orbitHex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        let value = UInt64(raw, radix: 16) ?? 0x7AD7ED
        self.init(red: CGFloat((value >> 16) & 0xFF) / 255,
                  green: CGFloat((value >> 8) & 0xFF) / 255,
                  blue: CGFloat(value & 0xFF) / 255, alpha: 1)
    }
}
