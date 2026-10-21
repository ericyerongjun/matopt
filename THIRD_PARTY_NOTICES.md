# Third-Party Notices

MatOpt redistributes source files and icon assets from the projects below. Each
component remains under its own licence; nothing in this file changes those
terms, and nothing here grants rights in MatOpt's own code.

Vendored copies, their exact commit pins and the reviewed source manifests
are recorded in [`vendor/UPSTREAM.md`](vendor/UPSTREAM.md). Run
`bash scripts/vendor_check.sh` to fail a build when a vendored copy has drifted
from the pin it was generated from.

MatOpt's own source does not yet declare a first-party licence (there is no root
`LICENSE` file). That is a separate decision and is deliberately not made here;
this file covers third-party components only.

| Component | Upstream | Pin | Licence | In MatOpt |
|---|---|---|---|---|
| DeepSeek Harness (`dsh`) | <https://github.com/ericyerongjun/deepseek-harness> | `639ed015397290b3745d163aafe02ffee4aa3f84` | MIT | Python session/telemetry and file-observation adaptations |
| ZCode | <https://github.com/zai-org/ZCode> | `29628c9acdb81b703bbd4080c207a0e7ce5e276e` (v3.14.3) | Apache-2.0 | Python tool scheduler, read-state/freshness adaptations, workspace shell helpers, active Zai palette and Markdown UI |
| Tailwind CSS | <https://github.com/tailwindlabs/tailwindcss> | v4.2.2 | MIT | Native ZCode CSS build dependency |

---

## 1. DeepSeek Harness — MIT

Python adaptations in `backend/app/agent/session/` and `tools/read_state.py`
preserve the documented session, token-meter, repair and file-observation
mechanisms. Their source headers and `session/UPSTREAM.md` identify the seams.
The earlier DSH frontend copies and Montserrat font assets were removed when
the native ZCode frontend replaced them.

MIT requires the copyright notice and this permission notice to accompany all
copies or substantial portions of the Software. Reproduced verbatim from
`LICENSE` at the pin:

MIT License

Copyright (c) 2026 DeepSeek

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## 2. ZCode — Apache License 2.0

The native frontend under `frontend/src/zcode` includes the full ZCode
stylesheet, composer, Markdown output, controls and workspace rendering
subtrees. Explicit MatOpt adapters replace desktop RPC services with HTTP/SSE
and retain mathematical chart capabilities. Copy and adaptation provenance is
recorded in `docs/reference/zcode-frontend-manifest.json`.

Adapted: `backend/app/agent/scheduler.py` transliterates
`apps/zcode-cli/packages/core/src/tool/scheduler.ts`; `tools/read_state.py`
adapts the read-file state/metadata and read/write/edit handler mechanisms at
`29628c9acdb81b703bbd4080c207a0e7ce5e276e` (v3.14.3). These reduced Python
ports identify their modifications in source; they do not copy ZCode's runtime.
The separate matcher/classifier table-vendoring scaffold remains reserved.
Apache-2.0 §4(d)
attribution carried forward from ZCode's `NOTICE.md` is in [`NOTICE`](NOTICE).

Reproduced verbatim from `LICENSE` at the pin:

                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS

   APPENDIX: How to apply the Apache License to your work.

      To apply the Apache License to your work, attach the following
      boilerplate notice, with the fields enclosed by brackets "[]"
      replaced with your own identifying information. (Don't include
      the brackets!)  The text should be enclosed in the appropriate
      comment syntax for the file format. We also recommend that a
      file or class name and description of purpose be included on the
      same "printed page" as the copyright notice for easier
      identification within third-party archives.

   Copyright 2026 Z.AI Co., Ltd

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.

---

## Other vendored components

These predate this file and keep their own notices; they are listed here so this
one is not mistaken for exhaustive.

- `skills/anthropic/` — Anthropic Agent Skills, with its own
  [`THIRD_PARTY_NOTICES.md`](skills/anthropic/THIRD_PARTY_NOTICES.md) and
  [`UPSTREAM.md`](skills/anthropic/UPSTREAM.md). Four document skills are
  source-available, not Apache-2.0.
- `backend/lambda_app/` — AMA-CMFAI/LAMBDA, which ships no upstream licence
  file; see [`backend/lambda/UPSTREAM.md`](backend/lambda/UPSTREAM.md).


## 3. Tailwind CSS — MIT

Tailwind compiles the full native ZCode stylesheet and component utility classes. License text from
Tailwind v4.2.2, as reproduced by pinned ZCode dependency notices:

MIT License

Copyright (c) Tailwind Labs, Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Native ZCode frontend source and Material Icon Theme

ZCode source is pinned at `29628c9acdb81b703bbd4080c207a0e7ce5e276e`, Apache-2.0.
Native source in `frontend/src/zcode/` replaces the earlier theme adaptation.
MatOpt HTTP/service adapters and special chart slots are marked in source.
AI Elements source retains Vercel copyright and Apache-2.0 notices.
Material Icon Theme assets are copied from ZCode public/material-icons.
Their original MIT license is retained in frontend/public/material-icons/LICENSE.txt.

---

## Runtime voice and avatar components (installed or downloaded, not vendored)

These are pip dependencies or model files fetched at first use into
`DATA_DIR/models`. They are listed because a built Docker image or data volume
contains them.

| Component | Source | Licence | Use |
|---|---|---|---|
| faster-whisper / CTranslate2 | PyPI | MIT | Speech recognition |
| Whisper `small` weights (Systran/faster-whisper-small) | Hugging Face | MIT | Speech recognition model |
| kokoro-onnx | PyPI | MIT | Speech synthesis runtime |
| Kokoro-82M v1.0 and v1.1-zh weights and voices | GitHub releases of kokoro-onnx; hexgrad on Hugging Face | Apache-2.0 | Speech synthesis models |
| misaki-fork (with jieba, pypinyin, cn2an) | PyPI | Apache-2.0 (dependencies MIT) | Mandarin and Japanese pronunciation |
| pyopenjtalk-plus and the OpenJTalk dictionary | PyPI | MIT / Modified BSD | Japanese readings |
| espeakng-loader (bundles libespeak-ng and its data) | PyPI | MIT wrapper; **espeak-ng is GPL-3.0-or-later** | Pronunciation for English and other languages |
| phonemizer | PyPI | GPL-3.0 | espeak-ng interface used by kokoro-onnx |
| three.js, @pixiv/three-vrm | npm | MIT | 3D avatar rendering |
| VRoid AvatarSample_F ("Vita"), `frontend/public/avatars/vita.vrm` | <https://opengameart.org/content/vroid-studio-cc0-models> (VRoid Project) | CC0 1.0; embedded VRM licence: any use, modification and redistribution, no credit required | Tutor avatar |

espeak-ng and phonemizer are GPL-3.0. Running MatOpt, or using it privately
or for coursework, is unaffected. Distributing a build that contains them
(for example a published Docker image) carries GPL obligations for those
components; set `SPEECH_ENABLED=false` and omit the voice packages to ship
without them.
