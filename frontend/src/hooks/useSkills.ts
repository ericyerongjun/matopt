import { useEffect, useState } from "react";
import { listSkills, type SkillInfo } from "../services/skills";

export function useSkills() {
    const [skills, setSkills] = useState<SkillInfo[]>([]);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        let attempt = 0;

        const load = () => {
            listSkills()
                .then((items) => {
                    if (!cancelled) {
                        setSkills(items);
                        setError(null);
                    }
                })
                .catch((err: unknown) => {
                    if (cancelled) return;
                    attempt += 1;
                    if (attempt < 4) {
                        window.setTimeout(load, 400 * attempt);
                        return;
                    }
                    setError(err instanceof Error ? err.message : "Could not load skills");
                });
        };

        load();
        return () => {
            cancelled = true;
        };
    }, []);

    return { skills, error };
}
