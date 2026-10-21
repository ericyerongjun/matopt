/**
 * Finishing touches for a built-in tutor, applied at load time: caricature
 * head proportions. (The resting smile is applied by the stage each frame.)
 */
import type { VRM } from "@pixiv/three-vrm";
import type { TutorPreset } from "./tutors.ts";

export { DEFAULT_TUTOR, isTutorId, TUTORS, type TutorId, type TutorPreset } from "./tutors.ts";

export function dressTutor(vrm: VRM, tutor: TutorPreset): void {
    vrm.humanoid.getRawBoneNode("head")?.scale.setScalar(tutor.headScale);
}
