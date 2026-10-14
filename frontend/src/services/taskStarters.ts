/** Welcome drafts for the inference and optimisation skills. */

export interface TaskStarter {
    label: string;
    skill: "statistical-inference" | "numerical-optimization";
    text: string;
}

export interface TaskStarterDraft {
    text: string;
    skills: string[];
}

export const TASK_STARTERS: readonly TaskStarter[] = [
    {
        label: "Compare two groups",
        skill: "statistical-inference",
        text: [
            "Compare the two groups in this table with a hypothesis test.",
            "",
            "group,score",
            "A,12",
            "A,15",
            "A,14",
            "A,13",
            "B,18",
            "B,17",
            "B,20",
            "B,16",
        ].join("\n"),
    },
    {
        label: "Fit a regression",
        skill: "statistical-inference",
        text: [
            "Fit a regression for this table and interpret the coefficients.",
            "",
            "hours,score",
            "1,42",
            "2,48",
            "3,55",
            "4,61",
            "5,70",
        ].join("\n"),
    },
    {
        label: "Minimize a function",
        skill: "numerical-optimization",
        text: "Minimize f(x, y) = (x - 1)^2 + 10*(y - x^2)^2. Try more than one start and check that the result is a minimum.",
    },
];

/** The draft a starter writes. It does not send the message. */
export function taskStarterDraft(starter: TaskStarter): TaskStarterDraft {
    return { text: starter.text, skills: [starter.skill] };
}
