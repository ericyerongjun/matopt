import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { parseSlashCommands, parseSlashSkillTokens } from "./slash.ts";

describe("parseSlashCommands", () => {
    it("splits leading skill and tool tokens", () => {
        const parsed = parseSlashCommands("/frontend-design /python make a landing page", {
            skills: ["frontend-design", "pdf"],
            tools: ["python", "web_search"],
        });
        assert.deepEqual(parsed.skills, ["frontend-design"]);
        assert.deepEqual(parsed.tools, ["python"]);
        assert.equal(parsed.rest, "make a landing page");
    });

    it("maps hyphenated tool tokens onto underscore names", () => {
        const parsed = parseSlashCommands("/web-search latest IMO", {
            skills: [],
            tools: ["web_search"],
        });
        assert.deepEqual(parsed.tools, ["web_search"]);
        assert.equal(parsed.rest, "latest IMO");
    });

    it("leaves unknown tokens in the message", () => {
        const parsed = parseSlashCommands("/not-a-command hello", {
            skills: ["pdf"],
            tools: ["python"],
        });
        assert.deepEqual(parsed.skills, []);
        assert.deepEqual(parsed.tools, []);
        assert.equal(parsed.rest, "/not-a-command hello");
    });
});

describe("parseSlashSkillTokens", () => {
    it("keeps the old names+rest shape", () => {
        const parsed = parseSlashSkillTokens("/pdf extract tables", ["pdf", "xlsx"]);
        assert.deepEqual(parsed.names, ["pdf"]);
        assert.equal(parsed.rest, "extract tables");
    });
});
