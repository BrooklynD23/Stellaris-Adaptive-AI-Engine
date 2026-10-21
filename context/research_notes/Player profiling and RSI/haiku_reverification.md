# Haiku re-verification of five RSI research leads

date: 2026-10-19
author: sae-scout (haiku)

Scope: re-opened primary sources for the five leads in `proposal.md` section 2. Existing notes were not used as evidence. Every URL below was opened in this session unless marked FETCH FAILED.

## Summary table

| # | Lead | Status (this check) | proposal.md status | Agree? |
|---|------|---------------------|--------------------|--------|
| 1 | MASS (arXiv 2610.12176) | VERIFIED | VERIFIED | Yes |
| 2 | AIDE2 (arXiv 2609.26457 + Weco blog) | PARTLY (blog and paper numbers differ; "Level 1" and "90% rejected" not found in paper) | VERIFIED, cite paper | Partly |
| 3 | Dream-RSI (arXiv 2609.14858) | PARTLY (1.62 absent; 1.79–2.43 present) | PARTLY VERIFIED | Yes |
| 4 | "RSI Loop Acceptance Gate" | NOT FOUND (no source with that name or the two stage questions) | NOT FOUND | Yes |
| 5a | TasteVal (LessWrong, 6 Oct 2026) | VERIFIED (org name not on page) | VERIFIED | Yes |
| 5b | Jack Clark RSI ~2027 (Import AI 455) | NOT FOUND at primary; secondary sources agree with proposal's numbers | INACCURATE | No (see below) |

## Per-lead detail

### 1. MASS (arXiv 2610.12176)

- Claim: one base model proposes, executes and self-evaluates multi-agent workflows; alternates evolutionary optimisation with SFT; 1.2–1.6x performance per output token after two cycles.
- Opened: https://arxiv.org/abs/2610.12176
- Status: VERIFIED. Submitted 8 Oct 2026. Authors: Hyunin Lee, Jinglue Xu, Jeffrey Seely, Donghyun Lee, Somayeh Sojoudi, Matei Zaharia, Yujin Tang.
- Quote: "1.2-1.6x higher performance per output tokens on four open-ended public benchmarks."
- Agree with proposal.md: yes. Sakana and Berkeley affiliations were not checked on the abstract page.

### 2. AIDE2 (Weco AI)

- Claim: Level 1 net-positive over 100 unsupervised steps in 8 days; 7 improved versions; KernelBench reward hacking 63% to 34%.
- Opened:
  - https://weco.ai/blog/first-evidence-of-recursive-self-improvement (dated 14 Jul 2026)
  - https://arxiv.org/abs/2609.26457 (submitted 22 Sep 2026)
  - https://arxiv.org/html/2609.26457 (partial read, first 100k chars)
- Status: PARTLY.
  - Blog: "100 consecutive steps"; "eight days of wall-clock time"; "seven successive improved versions"; "reward hacking rate from 63% to 34%" (AIDE0 63%, AIDE47 42%, AIDE85 34%); "believe AIDE2 to be on Level 1 of RSI."
  - Paper: "rate falls from 55% to 32%" (39% human baseline, 38 held-out pairs, 99 rewrite proposals, 7 accepted). Abstract: "seven successive improvements" in an "8-day run."
  - Not found in the paper: "Level 1", "net positive", "ignition", "90% rejected". The "90%" figure is in secondary coverage only, not in the blog page I opened.
- Quote (paper): "keeps the changes that perform best on hidden evaluations."
- Agree with proposal.md: partly. Its "cite the paper" advice is sound, but the paper's hacking numbers (55→32) differ from the blog's (63→34), and "Level 1" comes from the blog only. The blog's 63→34 should not be attributed to the paper.

### 3. Dream-RSI "Evolving Worlds" (arXiv 2609.14858)

- Claim: completed history used as replay simulator; 1.62–2.43x efficiency.
- Opened: https://arxiv.org/abs/2609.14858 and https://arxiv.org/html/2609.14858 (full HTML, first 100k chars). Submitted 14 Sep 2026; v2 6 Oct 2026; first author Tong Zheng.
- Status: PARTLY. "1.62" does not appear. Reported numbers:
  - KernelBench: "1.79×–2.43× fewer generations" vs Recursive Fixed Exploration.
  - KernelBench: "2.09× and 1.44× higher performance" (ConvDiv, ConvMax).
  - Lasso: "up to 162× fewer agent calls than SimpleTES" (different backbone).
  - Ablation: "1.59 when replay is restricted to the current round."
- Quote: "accumulated discovery history can act as a replay simulator over the realized search space."
- Caveat: no seeds or variance reported; each comparison appears to be one run.
- Agree with proposal.md: yes.

### 4. "The RSI Loop Acceptance Gate" (Stage 1 "did it work?" / Stage 2 "is it meaningful?")

- Claim: a named gate with two stages.
- Opened:
  - Search for the exact name and phrases: no source found.
  - https://arxiv.org/abs/2609.06396 (MetaRSI / RSI2; submitted 6 Sep 2026; Zihan Tan et al.). No gate criteria on the abstract page; "did it work" wording absent. Abstract: validated "on code and closed-form science, with no external teacher."
  - https://arxiv.org/abs/2607.13070 (Falsifiable Release Gates; submitted 11 Jul 2026; Deepak Soni). Page refers to "INV-1 through INV-6" (six, not five). Quote: "no action reaches an effector without a capability token minted by a control ring." Two-stage wording absent.
- Status: NOT FOUND. Note the proposal's "five invariants" is wrong by this page's count (six).
- Search-only nearest work (snippet, not opened): arXiv 2607.12254 "Self-Aware Recursively Self-Improving Agents for Personal Singularity" (promotion predicate: held-out gain plus safety and provenance checks). Add it to the nearest-work list if needed.
- Agree with proposal.md: yes on NOT FOUND. Correct the invariant count to six.

### 5a. TasteVal (P-Zero Research, LessWrong)

- Claim: TasteVal measures experimental research taste; Opus 5.5 at 2.30x.
- Opened: https://www.lesswrong.com/posts/bAGagjZAPgXEHmkAH/tasteval-measuring-the-experimental-research-taste-of-ai
- Status: VERIFIED. Dated 6 Oct 2026; authors "Ollie J" and "danesherbs" (Dane Sherburn). No organisation named on the page. Clark and named RSI timeline forecasts do not appear.
- Quote: "significantly exceeds our human baseline with a compute multiplier of 2.30x."
- Agree with proposal.md: yes.

### 5b. Jack Clark RSI estimate (Import AI 455)

- Claim: Clark estimated functional full RSI around 2027.
- Opened:
  - https://importai.substack.com/p/import-ai-455: HTTP 404.
  - https://importai.substack.com/archive: lists #466–#477 only. #455 not shown; pagination not followed.
  - Secondary (search snippets, not opened): hvylya.net and deeptechstars.substack.com report "60%+" by end of 2028 and "30%" for 2027.
- Status: NOT FOUND at primary. Secondary sources match the proposal's numbers, so the "INACCURATE" label is not supported by anything I opened.
- Agree with proposal.md: no. Change the status to "UNVERIFIED (primary not reachable)". The 2027 framing is wrong either way: the 30% figure is for 2027, the 60%+ is for 2028.

## Next actions

1. Open Import AI 455 via the Substack archive pagination, or the Substack post-ID URL, and confirm the 60%+ and 30% sentences.
2. Change proposal.md §2 row 5b from INACCURATE to UNVERIFIED until step 1 is done.
3. Correct proposal.md row 2 to say the "Level 1" and "90% rejected" claims come from the blog only, and cite the paper's 55→32 numbers separately.
