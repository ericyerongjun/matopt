/**
 * The 3D tutor: a VRM character that breathes, blinks, follows the camera,
 * thinks while the agent works and lip-syncs to its spoken replies.
 * Loaded lazily with three.js only when the avatar panel is open.
 */
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils, type VRM } from "@pixiv/three-vrm";
import { useVoiceStore, type Viseme } from "../voice/voiceStore.ts";

export const AVATAR_URL = "/avatars/vita.vrm";
const MOUTH: Array<keyof Viseme> = ["aa", "ih", "ou", "ee", "oh"];

export default function AvatarStage({ url = AVATAR_URL }: { url?: string }) {
    const host = useRef<HTMLDivElement>(null);
    const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

    useEffect(() => {
        const element = host.current;
        if (!element) return;
        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        element.appendChild(renderer.domElement);
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(24, 1, 0.1, 20);
        camera.position.set(0, 1.32, 1.65);
        const key = new THREE.DirectionalLight(0xffffff, 2.2);
        key.position.set(0.6, 1.8, 1.4);
        scene.add(key, new THREE.HemisphereLight(0xffffff, 0x8890a0, 1.4));
        const target = new THREE.Object3D();
        scene.add(target);

        let vrm: VRM | null = null;
        let frame = 0;
        let disposed = false;
        const clock = new THREE.Clock();
        const mouth: Viseme = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
        let nextBlink = 2 + Math.random() * 3, blink = 0, happy = 0, think = 0, listen = 0;

        const resize = () => {
            const { width, height } = element.getBoundingClientRect();
            if (!width || !height) return;
            renderer.setSize(width, height, false);
            camera.aspect = width / height;
            camera.updateProjectionMatrix();
        };
        const observer = new ResizeObserver(resize);
        observer.observe(element);
        resize();

        const loader = new GLTFLoader();
        loader.register((parser) => new VRMLoaderPlugin(parser));
        loader.load(url, (gltf) => {
            if (disposed) { VRMUtils.deepDispose(gltf.scene); return; }
            vrm = gltf.userData.vrm as VRM;
            VRMUtils.removeUnnecessaryVertices(gltf.scene);
            VRMUtils.combineSkeletons(gltf.scene);
            VRMUtils.rotateVRM0(vrm);
            // Relaxed rest pose instead of the T-pose the model is authored in.
            const bone = (name: Parameters<VRM["humanoid"]["getNormalizedBoneNode"]>[0]) => vrm?.humanoid.getNormalizedBoneNode(name);
            bone("leftUpperArm")?.rotation.set(0, 0, 1.22);
            bone("rightUpperArm")?.rotation.set(0, 0, -1.22);
            bone("leftLowerArm")?.rotation.set(0, 0, 0.18);
            bone("rightLowerArm")?.rotation.set(0, 0, -0.18);
            if (vrm.lookAt) vrm.lookAt.target = target;
            scene.add(vrm.scene);
            const head = bone("head");
            if (head) {
                const position = head.getWorldPosition(new THREE.Vector3());
                camera.position.set(0, position.y - 0.08, 1.7);
                camera.lookAt(0, position.y - 0.16, 0);
            }
            setStatus("ready");
        }, undefined, () => setStatus("error"));

        const animate = () => {
            frame = requestAnimationFrame(animate);
            if (document.hidden) return;
            const delta = Math.min(clock.getDelta(), 0.1);
            const time = clock.elapsedTime;
            if (vrm) {
                const { viseme, mood, speaking } = useVoiceStore.getState();
                const ease = (value: number, goal: number, rate: number) => value + (goal - value) * Math.min(1, delta * rate);
                happy = ease(happy, mood === "happy" ? 0.65 : speaking ? 0.12 : 0, 4);
                think = ease(think, mood === "thinking" ? 1 : 0, 2.5);
                listen = ease(listen, mood === "listening" ? 1 : 0, 3);
                for (const shape of MOUTH) mouth[shape] = ease(mouth[shape], speaking ? viseme[shape] : 0, 18);
                // Exposed (throttled) for tests and debugging: how open the mouth is right now.
                if (Math.floor(time * 10) % 2 === 0) element.dataset.mouth = Math.max(...MOUTH.map((shape) => mouth[shape])).toFixed(2);
                const expressions = vrm.expressionManager;
                if (expressions) {
                    for (const shape of MOUTH) expressions.setValue(shape, Math.min(1, mouth[shape]));
                    expressions.setValue("happy", happy);
                    expressions.setValue("relaxed", listen * 0.4);
                    nextBlink -= delta;
                    if (nextBlink <= 0) { blink = 1; nextBlink = 2.2 + Math.random() * 3.5; }
                    blink = Math.max(0, blink - delta * 7);
                    expressions.setValue("blink", Math.min(1, blink * 2) * (1 - happy * 0.6));
                }
                const sway = reduceMotion ? 0 : 1;
                const spine = vrm.humanoid.getNormalizedBoneNode("spine");
                const chest = vrm.humanoid.getNormalizedBoneNode("chest");
                const neck = vrm.humanoid.getNormalizedBoneNode("neck");
                const head = vrm.humanoid.getNormalizedBoneNode("head");
                if (spine) spine.rotation.set(Math.sin(time * 1.1) * 0.012 * sway + listen * 0.05, Math.sin(time * 0.45) * 0.03 * sway, 0);
                if (chest) chest.rotation.x = Math.sin(time * 2.2) * 0.015 * sway;
                if (neck) neck.rotation.set(-think * 0.06, Math.sin(time * 0.6) * 0.04 * sway, think * 0.1);
                if (head) head.rotation.set(Math.sin(time * 0.9) * 0.02 * sway - think * 0.08, think * -0.15, think * 0.08);
                // Eyes follow the camera, glancing up and aside while thinking.
                target.position.set(camera.position.x + think * 0.35, camera.position.y + think * 0.25, camera.position.z);
                vrm.update(delta);
            }
            renderer.render(scene, camera);
        };
        animate();

        return () => {
            disposed = true;
            cancelAnimationFrame(frame);
            observer.disconnect();
            if (vrm) VRMUtils.deepDispose(vrm.scene);
            renderer.dispose();
            renderer.domElement.remove();
        };
    }, [url]);

    return <div ref={host} className="matopt-avatar-stage" data-status={status} role="img"
        aria-label={status === "error" ? "The avatar could not be loaded" : "MatOpt tutor avatar"}>
        {status === "loading" && <span className="matopt-avatar-note">Loading avatar…</span>}
        {status === "error" && <span className="matopt-avatar-note">Avatar unavailable</span>}
    </div>;
}
