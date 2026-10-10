import { useEffect, useMemo, useState } from "react";
import {
    fallbackTools,
    listSkills,
    listTools,
    toCommands,
    type SkillInfo,
    type ToolInfo,
} from "../services/skills";

export function useSkills() {
    const [skills, setSkills] = useState<SkillInfo[]>([]);
    const [tools, setTools] = useState<ToolInfo[]>(() => fallbackTools());
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        let attempt = 0;
        let timer: number | undefined;

        const load = () => {
            Promise.allSettled([listSkills(), listTools()]).then((results) => {
                if (cancelled) return;
                const [skillRes, toolRes] = results;
                const skillOk = skillRes.status === "fulfilled";
                const toolOk = toolRes.status === "fulfilled";
                if (skillOk) setSkills(skillRes.value);
                if (toolOk) setTools(toolRes.value);
                else setTools(fallbackTools());
                if (skillOk && toolOk) {
                    setError(null);
                    return;
                }
                attempt += 1;
                if (attempt < 4) {
                    timer = window.setTimeout(load, 400 * attempt);
                    return;
                }
                setError(!skillOk && !toolOk ? "Could not load skills and tools" : !skillOk ? "Could not load skills" : "Could not load tools");
            });
        };

        load();
        return () => {
            cancelled = true;
            window.clearTimeout(timer);
        };
    }, []);

    const commands = useMemo(() => toCommands(skills, tools), [skills, tools]);

    return { skills, tools, commands, error };
}
