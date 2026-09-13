import {
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Pill,
  Row,
  Stack,
  Stat,
  Table,
  Text,
  UsageBar,
} from "cursor/canvas";

const SOURCE =
  "Source: step diagnostics JSON · 2026-09-13 01:25–03:40 UTC · model gemma-4-q4km:26b · num_ctx 32768";

export default function LastExecutionDiagnostics() {
  return (
    <Stack gap={24}>
      <Stack gap={8}>
        <H1>Last sprint execution</H1>
        <Text tone="secondary">
          Four Flutter store cards on gemma-4-q4km:26b. Wall clock 2h 15m.
          Developer steps keep writing, then stop at the 6-iteration cap
          without moving the card. The last delete-tests step timed out in
          Ollama after 301s with zero tokens.
        </Text>
        <Text size="small" tone="tertiary">
          {SOURCE}
        </Text>
      </Stack>

      <Grid columns={4} gap={16}>
        <Stat value="2h 15m" label="Wall clock" />
        <Stat value="85%" label="Time in Ollama" tone="warning" />
        <Stat value="9 / 25" label="Dev steps hit 6/6 cap" tone="danger" />
        <Stat value="0 / 23" label="Lint-clean verifies" tone="danger" />
      </Grid>

      <Callout tone="warning" title="Throughput is generation-bound, not tools">
        Across 38 traces, Ollama used 111 of 130 summed minutes. Decode ran
        at ~14 eval tok/s (78 min). Tools used 2.5 min. Prefill after the
        first call is already cheap (~1.9k prompt tok/s). Speeding tools or
        adding more list_dir calls will not move the needle.
      </Callout>

      <Stack gap={8}>
        <H2>Where the 130 summed minutes went</H2>
        <Text size="small" tone="tertiary">
          Sum of unique trace durations in the window (sequential cards;
          wall clock was 135 min). Eval = model generation; prompt eval =
          prefill.
        </Text>
        <UsageBar
          total={130.3}
          topLeftLabel="Time split (minutes)"
          topRightLabel="Eval 78 · Prefill 14 · Overhead 17 · Tools 2.5"
          segments={[
            { id: "eval", value: 77.6 },
            { id: "prefill", value: 14.0 },
            { id: "other", value: 17.2 },
            { id: "tools", value: 2.5 },
          ]}
        />
      </Stack>

      <Stack gap={8}>
        <H2>Minutes by card</H2>
        <BarChart
          height={220}
          categories={[
            "Edit UI",
            "Save/retrieve tests",
            "Update tests",
            "Delete tests",
          ]}
          series={[
            {
              name: "Trace duration (min)",
              data: [38.7, 10.7, 50.1, 30.8],
              tone: "neutral",
            },
            {
              name: "Ollama (min)",
              data: [34.3, 9.0, 43.4, 23.8],
              tone: "warning",
            },
          ]}
          valueSuffix=" min"
        />
        <Text size="small" tone="tertiary">
          {SOURCE}
        </Text>
      </Stack>

      <Grid columns="1.2fr 1fr" gap={20}>
        <Stack gap={8}>
          <H2>Developer step length</H2>
          <BarChart
            height={240}
            categories={[
              "1",
              "2",
              "3",
              "4",
              "5",
              "6",
              "7",
              "8",
              "9",
              "10",
              "11",
              "12",
              "13",
              "14",
              "15",
              "16",
              "17",
              "18",
              "19",
              "20",
              "21",
              "22",
            ]}
            series={[
              {
                name: "Developer step duration (s)",
                data: [
                  617, 436, 303, 326, 325, 109, 359, 175, 224, 378, 228,
                  354, 252, 121, 280, 365, 147, 244, 173, 338, 262, 298,
                ],
                tone: "info",
              },
            ]}
            valueSuffix=" s"
          />
          <Text size="small" tone="tertiary">
            Source: 22 complete Developer traces &gt;5s · median 280s · mean
            276s · left to right in wall-clock order
          </Text>
        </Stack>
        <Stack gap={8}>
          <H2>Outcome mix</H2>
          <Table
            headers={["Signal", "Count", "Read"]}
            columnAlign={["left", "right", "left"]}
            rows={[
              ["Dev steps at 6/6 iterations", "9", "Cap stops lane move"],
              ["Dev steps at 3 iterations", "8", "Wrote then verify-stop"],
              ["PO steps (update_board only)", "6", "~2.5 min each"],
              ["Skipped duplicate flutter test", "17", "Burned an LLM turn"],
              ["apply_patch failures", "8", "Mismatch / retry"],
              ["Write success / attempted", "27 / 35", "77% write success"],
              ["Ollama timeouts", "2", "301s, 0 tokens"],
              ["Plan / text rejections", "0", "Not the bottleneck"],
            ]}
            rowTone={[
              "danger",
              "warning",
              "warning",
              "warning",
              "danger",
              "neutral",
              "danger",
              "success",
            ]}
            striped
          />
        </Stack>
      </Grid>

      <H2>Card-by-card</H2>
      <Table
        headers={[
          "Card",
          "Dev / PO",
          "Minutes",
          "Hit cap",
          "Last known state",
        ]}
        columnAlign={["left", "right", "right", "right", "left"]}
        rows={[
          [
            "Store Edit UI (2/2)",
            "5 / 2",
            "39",
            "4",
            "PO moved board; Developer kept rewriting UI + repo",
          ],
          [
            "Repository tests: save/retrieve",
            "3 / 0",
            "11",
            "1",
            "Explore-stuck, then write, then failed patch",
          ],
          [
            "Repository tests: update",
            "12 / 3",
            "50",
            "2",
            "Same test file rewritten ~10 times; verify never completes",
          ],
          [
            "Repository tests: delete",
            "5 / 1",
            "31+",
            "2",
            "Still running at 03:40; iter 2 timed out at 301s",
          ],
        ]}
        rowTone={["warning", "warning", "danger", "danger"]}
        striped
      />

      <Stack gap={8}>
        <H2>Failure modes that actually cost time</H2>
        <Grid columns={2} gap={12}>
          <Card>
            <CardHeader>Iteration cap vs verify budget</CardHeader>
            <CardBody>
              <Text>
                Typical first pass: list_dir → read_file → add_subtasks →
                write_file → run_command. That is already 5 of 6 iterations
                before a second verify or update_board. Diagnostics then
                say the card stayed in In Progress because the agent hit
                the LLM iteration limit after writing. Why-card-stayed was
                that message on almost every Developer complete trace.
              </Text>
            </CardBody>
          </Card>
          <Card>
            <CardHeader>Rewrite loop on one test file</CardHeader>
            <CardBody>
              <Text>
                Update-tests spent 50 minutes and 12 Developer steps on
                test/data/store_repository_test.dart. Pattern:
                write_file → skipped duplicate flutter test → stop. The
                duplicate-command cache is working, but the model still
                spends a full generation (~3 min, 2–4k eval tokens) to
                “run” a test that is immediately skipped.
              </Text>
            </CardBody>
          </Card>
          <Card>
            <CardHeader>PO is a 2.5-minute update_board</CardHeader>
            <CardBody>
              <Text>
                All six Product Owner traces: 1 iteration, one tool
                (update_board), 11–12k prompt tokens, 547–1572 eval
                tokens, 107–179s. Prefill ~31–34s then a long decode.
                That is ~15 minutes of the window to bounce the lane, not
                to specify work.
              </Text>
            </CardBody>
          </Card>
          <Card>
            <CardHeader>Prefill tax on a 32k window</CardHeader>
            <CardBody>
              <Text>
                Sampling is num_ctx 32768 while typical prompts are
                5–12k tokens (peaks ~64k reported across a step, still
                under the window). First call of a step: ~18s prefill
                (~2.9 ms/tok). Later calls: ~3.6s (~0.54 ms/tok) thanks
                to KV cache. Oversized ctx still hurts the cold start of
                every step.
              </Text>
            </CardBody>
          </Card>
        </Grid>
      </Stack>

      <Divider />

      <H2>Improvements, ranked by expected wall-clock win</H2>
      <Table
        headers={["Priority", "Change", "Why it shows up here", "Est. save"]}
        columnAlign={["left", "left", "left", "left"]}
        rows={[
          [
            "P0",
            "Do not spend an LLM turn on skipped-duplicate verify. If write succeeded and flutter test already passed this step, advance verify / lane without another generate.",
            "17 skipped duplicate tests; many 3-iter steps are write + skip + stop.",
            "Cut 3-iter rewrite cycles (~4–6 min each)",
          ],
          [
            "P0",
            "After a successful write, reserve or auto-extend iterations for update_board instead of stopping at 6/6 with files written and lane unchanged.",
            "9/25 Developer steps exhausted max=6; card never left In Progress from the Developer.",
            "Remove PO round-trips (~2.5 min × 6)",
          ],
          [
            "P0",
            "Stop the same-file rewrite loop: if write_file content hash is unchanged or only churns the same test, mark verify done or Needs User instead of another In Progress step.",
            "Update-tests: 12 Developer steps, ~10 rewrites of one file, cycle 1→12.",
            "Largest card was 50 min",
          ],
          [
            "P1",
            "Deterministic PO lane move (or a tiny num_predict / 8k ctx PO prompt) when the only action is update_board.",
            "PO never used tools other than update_board; eval was 39–115s.",
            "~12–14 of 15 PO minutes",
          ],
          [
            "P1",
            "Match num_ctx to packed prompt (8–16k), not 32k. Keep-alive is already -1s.",
            "First-call prefill 18s vs later 3.6s; prompts far below 32k.",
            "~15s × ~29 step starts",
          ],
          [
            "P1",
            "Prefer write_file for whole test files; treat apply_patch mismatch as immediate rewrite, not 2–3 more generate-and-fail turns.",
            "8 patch failures; several steps used remaining budget on 48–379 char replaces.",
            "1–2 min per failed patch chain",
          ],
          [
            "P2",
            "Fail Ollama sooner than 300s when eval tokens stay 0; retry once with lower num_predict rather than sitting in ollama_wait.",
            "Last delete-tests step: timeout at 301s, then a second start; 10 min stall.",
            "Recover ~5 min per hang",
          ],
          [
            "P2",
            "Skip add_subtasks / extra list_dir on well-specified unit-test cards; seed the test path from the workspace map.",
            "First pass often burns 3 explore turns (exploreMax=3) before the write.",
            "1–2 iterations per new card",
          ],
        ]}
        rowTone={[
          "danger",
          "danger",
          "danger",
          "warning",
          "warning",
          "warning",
          "info",
          "info",
        ]}
        striped
      />

      <Stack gap={8}>
        <H3>What is already fine</H3>
        <Row gap={8} wrap>
          <Pill tone="success" size="sm">
            Decode ~14 tok/s for 26B Q4
          </Pill>
          <Pill tone="success" size="sm">
            Tool time 2%
          </Pill>
          <Pill tone="success" size="sm">
            Duplicate command cache works
          </Pill>
          <Pill tone="success" size="sm">
            No plan/text rejections
          </Pill>
          <Pill tone="success" size="sm">
            KV cache after first call
          </Pill>
          <Pill tone="warning" size="sm">
            Lint never reported clean
          </Pill>
        </Row>
        <Text tone="secondary">
          Local model speed is adequate. The loop policy is what made a
          set of small repository tests take two hours: 6-iteration steps
          that write, skip a cached test, stop, then start over, with a
          full PO generate just to call update_board.
        </Text>
      </Stack>
    </Stack>
  );
}
