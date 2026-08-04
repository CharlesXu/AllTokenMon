# Third-party notices

All Token Monitor has no install-time or runtime package dependencies. Its
Python implementation uses the standard library plus the vendored decoder
components listed below.

| Component | Version / baseline | How it is used | Bundled | License |
| --- | --- | --- | --- | --- |
| Tokscale | `22b9dbd5107a7eed15416c814f25c5ef72079cc8` | Format and behavior reference | No | MIT |
| pywasm | 0.4.8 | WebAssembly interpreter for Zed's local compressed thread data | Yes, source | WTFPL |
| zstdpy | `91642caf3168fcc99feeb2878e5c8f788b658db9` | Precompiled Zstandard decoder artifact | Yes, WebAssembly binary | 0BSD |
| Zstandard | 1.4.5 / `b706286adbba780006a47ef92df0ad7a785666b6` | Decoder embedded in the zstdpy artifact | Yes, inside the WebAssembly binary | BSD |

## Tokscale independence

All Token Monitor does not import, execute, download, install, or call
Tokscale. It can start, scan native runtime data, aggregate records, and render
reports on a machine where Tokscale has never been installed.

For backward-compatible data discovery, four adapters can also read compatible
cache files if they already exist under conventional Tokscale cache paths:
Cursor, Antigravity, Trae, and Warp. Codex discovery likewise recognizes an
optional Tokscale headless export directory. These are passive local-file
inputs only: their absence does not prevent All Token Monitor or its native
runtime adapters from working, and All Token Monitor never asks Tokscale to
create or update them.

Tokscale source was studied as a frozen format and behavior reference, so this
project does not claim a strict clean-room provenance relative to Tokscale.
Tokscale is not bundled and is not a runtime dependency. Its notice is retained
below for attribution and MIT-license compliance.

## Development and CI tooling (not redistributed)

The following direct tools run only in development or GitHub Actions. They are
not included in the Skill ZIP and are not required by end users.

| Tool | Use | License |
| --- | --- | --- |
| [coverage.py](https://github.com/nedbat/coveragepy) | Test coverage measurement | Apache-2.0 |
| [actions/checkout](https://github.com/actions/checkout) | CI repository checkout | MIT |
| [actions/setup-python](https://github.com/actions/setup-python) | CI Python provisioning | MIT |

Python, operating-system facilities, and the agent runtimes whose local data is
read are platforms or interoperability targets, not redistributed components.
Their names in adapters and documentation do not imply that their software is
bundled with or required by All Token Monitor.

## Tokscale

Copyright (c) 2025 Junho Yeo

Licensed under the MIT License.

Source baseline:
<https://github.com/junhoyeo/tokscale/tree/22b9dbd5107a7eed15416c814f25c5ef72079cc8>

### MIT License

Copyright (c) 2025 Junho Yeo

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

## pywasm 0.4.8

The files under `scripts/alltokenmon/_vendor/pywasm/` come from the pywasm
0.4.8 PyPI distribution:
<https://pypi.org/project/pywasm/0.4.8/>.

The upstream project is <https://github.com/mohanson/pywasm>. Release version
0.4.8 is recorded at commit
[`1fc9fc8ec4c57e785d63a40c34184cd22fc8f4de`](https://github.com/mohanson/pywasm/tree/1fc9fc8ec4c57e785d63a40c34184cd22fc8f4de).
The vendored copy changes only top-level `pywasm` imports to package-relative
imports. It is licensed under the WTFPL.

### DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE

```text
            DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE
                    Version 2, December 2004

 Copyright (C) 2004 Sam Hocevar <sam@hocevar.net>

 Everyone is permitted to copy and distribute verbatim or modified
 copies of this license document, and changing it is allowed as long
 as the name is changed.

            DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE
   TERMS AND CONDITIONS FOR COPYING, DISTRIBUTION AND MODIFICATION

  0. You just DO WHAT THE FUCK YOU WANT TO.
```

## zstdpy decoder artifact

`scripts/alltokenmon/_vendor/zstddec.wasm` comes from zstdpy commit
[`91642caf3168fcc99feeb2878e5c8f788b658db9`](https://github.com/dholth/zstdpy/tree/91642caf3168fcc99feeb2878e5c8f788b658db9).
Its pinned SHA-256 is
`de7e4cb73ab269db0450b6c5561e52a0f816704ec02eddcee2da548aeb88a0fe`.
The zstdpy-specific wrapper portions are licensed under the 0BSD license.

### Zero-Clause BSD

```text
Permission to use, copy, modify, and/or distribute this software
for any purpose with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL
DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA
OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
```

## Zstandard decoder embedded in zstdpy

The WebAssembly artifact embeds Zstandard 1.4.5's single-file decoder.
Upstream tag `v1.4.5` resolves to commit
[`b706286adbba780006a47ef92df0ad7a785666b6`](https://github.com/facebook/zstd/tree/b706286adbba780006a47ef92df0ad7a785666b6).
It is licensed under the BSD license below.

### BSD License

```text
BSD License

For Zstandard software

Copyright (c) 2016-present, Facebook, Inc. All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

 * Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

 * Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

 * Neither the name Facebook nor the names of its contributors may be used to
   endorse or promote products derived from this software without specific
   prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

The in-tree copy at `scripts/alltokenmon/_vendor/NOTICE.md` accompanies the
vendored files; this top-level notice is the distribution-wide attribution.
