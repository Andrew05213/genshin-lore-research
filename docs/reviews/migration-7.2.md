# 7.2 bounded migration — work in progress

This branch migrates the existing 35-input bounded build. It does not claim
coverage of every upstream gameplay file or publish a new accepted release.
The deployed 7.1 database and client configuration remain unchanged.

## Fixed input

- Repository: `DimbreathBot/AnimeGameData`
- Commit: `d54f6f0d423ded9d055bb740a3e5f3aa8f2667f1`
- Tree: `5b334e711319726c504da95dbfe6ebb26baaa7d3`
- Snapshot: `anime-game-data:7.2:d54f6f0d423ded9d055bb740a3e5f3aa8f2667f1`
- Implementation starting point: `7156c217551c6d2716dad730da3e7a873f4c1dc5`.

All 156,131 extracted files were verified against their exact Git blob IDs.
Git checkout is clean. The initial lazy Git checkout was interrupted after a
stalled fetch; a commit-pinned archive was then verified and materialized.
The acquisition failure is retained locally.

## Current execution status

Initial source diagnostics passed for:

- Schema20 core: 33 provider contracts, assembled from 23 freshly built shards;
- Character foundation, text, lore and voice;
- NPC, Dialog and Reminder;
- Document, GCG, LoadingTips and Mail;
- Quest/Talk: 4,543 Quest files, 57,643 Talk files, 459,828 dialogue nodes.
- Quest Narrative/Review/binding, CodexQuest and physical Talk closure;
- R0, Readable, all 4,839 Subtitle files;
- all nine Event inputs and all six Native runs.

These are initial component/source-oracle checks, not a final unified build.
Later implementation/contract changes invalidate automatic reuse of old v3
stage receipts. The final build must run after the code and inputs freeze.

R0's initial artifact and independent source oracle passed, but its enclosing
stage **failed** because a shared script changed while it ran. No successful
stage receipt was issued. The artifact, oracle and failure diagnostics remain
local; they must not be promoted or used as a qualified downstream dependency.

The first core assembly also failed at the oracle boundary because a provider
`version` variable overwrote the game-version selector. The corrected retry
passed; both records and the first artifact are retained.

## Scope and implementation

`scripts/build.py --source-version 7.2` selects explicit pinned input contracts.
All existing stages are wired for the pinned 7.2 source. The frozen-code
`--stage all` qualification is separate from those source diagnostics.
The default remains `7.1`; installers and deployed client settings are unchanged.

`scripts/maintenance/prepare_source_contracts.py` regenerates declared source
observations. It does not grant product/release qualification. Existing 7.1
scopes and SQL definitions are preserved. New source contracts are separate.

Source-authored `subQuests`, `talks`, `dialogList`, `nextDialogs` and Voice
fields have explicit 7.2 decoding and independent reconstruction. Unknown
registered Talk shapes remain unresolved. Physical navigation does not prove
speaker identity or narrative chronology.

AvatarRename no longer supplies `renameID`: `relatedAvatarID` is retained
under its own field name, never relabelled as that historical ID. Opaque
Document arrays remain candidate observations with an unresolved namespace.
Raw field correspondence does not prove cross-snapshot logical identity.

Photo root and page selectors now disagree. Their disjoint physical union
contains 24 position references and remains explicitly partial; it does not
prove logical activity equivalence or a narrative order. All 240 unresolved
Talk roots were inventoried: 239 contain only `talkId`, one only
`defaultVocalBoneName`, and none has `talkContentTextMapHash`. Their raw records
remain addressable without inventing missing dialogue text.

The first frozen-build attempt was deliberately interrupted early after
review found that the public continuation checker still asked fixed 7.1
questions. The checker now derives its question version from the exact corpus
snapshot. The interrupted output is not qualified; the next attempt uses a
new output directory and freshly computed stage identities.

A later `e1b6d70` full command built and independently verified all 35 inputs
and the 6,649,974,784-byte index, including both complete index oracles, but
**exited 1** at runtime smoke after 32:17.34. It did not build the semantic
overlay. Its registry omitted the executable proof-ceiling status, and the
Codex reading view still checked the former masked label instead of 7.2
`textType`. The original execution/failure reports remain local; this is not
recorded as a successful full command.

The retry restores explicit local source-verified capability statuses without
copying old artifact/run acceptance, preserving the physical/raw proof ceiling.
Codex checks its actual source-version label key. Native 7.1 behavior is retained.

Document 7.2 now uses `document-7.2-evidence-v2`: unresolved Localization
candidates carry independently checked, source-exported Readable paths. The
relation remains `mapping_unresolved`; this is not confirmation of a book's
logical ownership. Reading context and saved cards label the title as a
candidate, while the text and ranges retain exact file identity. This closes
the body-delivery gap without interpreting the opaque array as an authored
namespace. A real source build/oracle and a target-omission negative probe
passed. The final full rebuild must use this updated contract.

## Remaining work

- Build the complete unified Research index and new semantic overlay.
- Qualify the generated runtime, fulltext/dialogue continuation and citations.
- Run final frozen-code source verification and regression; retain failures.

No full migration, runtime migration, accepted release or client default
switch has been claimed at this checkpoint. Game text and generated data are
not included in this source repository.
