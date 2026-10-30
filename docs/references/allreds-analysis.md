# AllReds analysis

The supplied AllReds executable is compiled code; this repository does not contain the source archive. Its hash/size match the master prompt. The Rust CLI now reproduces the DOL header and changed-byte comparison. A focused PPC branch decoder identifies 32 modified instructions in the original text sections whose new relative branch destinations enter the added section. These are strong hook candidates, but the original surrounding functions and hook semantics remain unidentified.

| Original file offset | Patched instruction PC | New branch destination |
|---:|---:|---:|
| `0x1A448` | `0x8001EF08` | `0x806BEC68` |
| `0x1D440` | `0x80021F00` | `0x806BFC3C` |
| `0x94780` | `0x80099240` | `0x806BFE40` |
| `0x94BAC` | `0x8009966C` | `0x806BFE74` |
| `0x9DD2C` | `0x800A27EC` | `0x806BF24C` |
| `0x9DEEC` | `0x800A29AC` | `0x806BF24C` |
| `0x15A4DC` | `0x8015EF9C` | `0x806BED28` |
| `0x161508` | `0x80165FC8` | `0x806BF87C` |
| `0x23CC88` | `0x80241748` | `0x806BFD40` |
| `0x24194C` | `0x8024640C` | `0x806BF87C` |
| `0x2A6AF8` | `0x802AB5B8` | `0x806BFFD4` |
| `0x2AA2E0` | `0x802AEDA0` | `0x806BF740` |
| `0x2AB314` | `0x802AFDD4` | `0x806BF3CC` |
| `0x2ABD94` | `0x802B0854` | `0x806BFFF0` |
| `0x2ADF80` | `0x802B2A40` | `0x806BF6D0` |
| `0x2C09CC` | `0x802C548C` | `0x806BFD04` |
| `0x2C413C` | `0x802C8BFC` | `0x806BF774` |
| `0x338170` | `0x8033CC30` | `0x806BF978` |
| `0x339BC4` | `0x8033E684` | `0x806BF288` |
| `0x339D60` | `0x8033E820` | `0x806BE8F8` |
| `0x33F074` | `0x80343B34` | `0x806BF108` |
| `0x33F134` | `0x80343BF4` | `0x806BF6EC` |
| `0x33F15C` | `0x80343C1C` | `0x806BF638` |
| `0x33F994` | `0x80344454` | `0x806BE628` |
| `0x340590` | `0x80345050` | `0x806BF054` |
| `0x341F8C` | `0x80346A4C` | `0x806BF2F8` |
| `0x35F358` | `0x80363E18` | `0x806BEDC0` |
| `0x3B8914` | `0x803BD3D4` | `0x806BFA7C` |
| `0x3C5BB0` | `0x803CA670` | `0x806BEFE4` |
| `0x3C7AC8` | `0x803CC588` | `0x806BF710` |
| `0x4B1F98` | `0x804B6A58` | `0x806BEDC0` |
| `0x4B999C` | `0x804BE45C` | `0x806BEEA4` |

The CLI's branch destinations are calculated from DOL section virtual addresses and the PowerPC relative branch displacement. Another changed conditional branch at file offset `0x2A863C` now targets `0x802AD160`, inside the original text section. Additional edits replace calls or conditional branches with NOPs, adjust operands, and add a data pointer referring into the added section. These facts describe binary structure only; no function names or gameplay responsibilities are inferred yet.

Do not redistribute or copy the modified executable or its code. The goal is independent architectural understanding.
