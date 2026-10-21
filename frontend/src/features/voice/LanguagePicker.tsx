import { useEffect, useState } from "react";
import { Languages } from "lucide-react";
import { getSpeechStatus, type SpeechLanguage } from "../../services/speech.ts";
import { useVoiceStore } from "./voiceStore.ts";

const FALLBACK: SpeechLanguage[] = [{ code: "en", label: "English", voice: "" }, { code: "zh", label: "中文", voice: "" }];

/** Reply and voice language: Auto follows the user; a choice is stated to the model and used for speech. */
export function LanguagePicker({ disabled = false }: { disabled?: boolean }) {
    const { language, setPreference } = useVoiceStore();
    const [languages, setLanguages] = useState<SpeechLanguage[]>(FALLBACK);
    useEffect(() => { void getSpeechStatus().then((status) => { if (status.languages?.length) setLanguages(status.languages); }).catch(() => undefined); }, []);
    return <label className="matopt-language-picker" title="Reply and voice language">
        <Languages aria-hidden="true" />
        <span className="sr-only">Reply language</span>
        <select value={language ?? ""} disabled={disabled} onChange={(event) => setPreference("language", event.target.value || null)}>
            <option value="">Auto</option>
            {languages.map((lang) => <option key={lang.code} value={lang.code}>{lang.label}</option>)}
        </select>
    </label>;
}
