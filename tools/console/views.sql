-- SAE offline console views over <run_dir>/bus.jsonl (legal bus only; bus.god.jsonl is never read).
-- {{BUS}} is substituted by query.py with a single-quote-escaped path. Statements are split on ";" + newline.
CREATE VIEW v_events AS
SELECT * FROM read_json('{{BUS}}', format='newline_delimited', ignore_errors=true,
  columns={v:'INTEGER', run_id:'VARCHAR', seq:'BIGINT', t_wall:'VARCHAR', t_mono_ns:'BIGINT',
           game_date:'VARCHAR', decision_id:'VARCHAR', type:'VARCHAR', fow:'VARCHAR', payload:'JSON'})
WHERE v = 1;

CREATE VIEW v_acks_final AS
SELECT decision_id, game_date,
       CAST(payload->>'country' AS INTEGER) AS country,
       CAST(payload->>'seq' AS BIGINT) AS batch_seq,
       payload->>'status' AS status, payload->>'code' AS code, seq AS event_seq
FROM v_events WHERE type = 'intent_ack'
QUALIFY row_number() OVER (PARTITION BY payload->>'country', payload->>'seq' ORDER BY seq DESC) = 1;

CREATE VIEW v_belief AS
SELECT e.decision_id, e.game_date, e.seq AS event_seq, p.key AS archetype,
       CAST(p.value AS DOUBLE) AS posterior,
       CAST(e.payload->>'entropy' AS DOUBLE) AS entropy,
       CAST(e.payload->>'change_score' AS DOUBLE) AS change_score_diagnostic
FROM v_events e,
     UNNEST(map_entries(CAST(e.payload->'posterior' AS MAP(VARCHAR, DOUBLE)))) AS u(p)
WHERE e.type = 'belief_update';

CREATE VIEW v_intents AS
SELECT e.decision_id, e.game_date,
       CAST(e.payload->>'$.batch.seq' AS BIGINT) AS batch_seq,
       CAST(i->>'country' AS INTEGER) AS country, i->>'intent_id' AS intent_id, i->>'kind' AS kind,
       i AS intent, 'emitted' AS disposition, CAST(e.payload->>'$.receipt.ok' AS BOOLEAN) AS emit_ok,
       e.payload->>'$.receipt.error' AS emit_error, NULL AS rule
FROM v_events e, UNNEST(CAST(json_extract(e.payload, '$.batch.intents') AS JSON[])) AS u(i)
WHERE e.type = 'intent_emitted'
UNION ALL
SELECT decision_id, game_date, NULL, CAST(payload->>'$.intent.country' AS INTEGER),
       payload->>'$.intent.intent_id', payload->>'$.intent.kind', payload->'intent',
       CASE WHEN ((payload->>'rule') = 'deferred' OR starts_with(coalesce(payload->>'detail', ''), 'deferred:')) THEN 'deferred' ELSE 'rejected' END, NULL, NULL,
       payload->>'rule'
FROM v_events WHERE type = 'intent_rejected';

CREATE VIEW v_health AS
SELECT seq, game_date, payload->>'kind' AS kind, payload->>'component' AS component,
       payload->>'state' AS state, payload->>'level' AS level, payload->>'trigger' AS "trigger",
       payload->>'detail' AS detail, payload->'metrics' AS metrics
FROM v_events WHERE type = 'health';

CREATE VIEW v_control AS
SELECT seq, game_date, payload->>'$.command.command_id' AS command_id,
       payload->>'$.command.operator' AS operator, payload->>'$.command.reason' AS reason,
       payload->>'$.command.command.cmd' AS cmd,
       CAST(payload->>'$.result.accepted' AS BOOLEAN) AS accepted,
       payload->>'$.result.error' AS error, payload->>'$.result.detail' AS detail,
       payload->>'$.result.status' AS status
FROM v_events WHERE type = 'control';

CREATE VIEW v_decisions AS
WITH d AS (SELECT DISTINCT decision_id FROM v_events WHERE decision_id IS NOT NULL),
obs AS (SELECT decision_id, min(game_date) AS observation_date FROM v_events
        WHERE type = 'observation' GROUP BY decision_id),
bel AS (SELECT decision_id, arg_max(archetype, posterior) AS top_archetype, max(posterior) AS max_posterior,
               any_value(entropy) AS entropy
        FROM v_belief GROUP BY decision_id),
strat AS (SELECT decision_id, payload->>'strategy' AS strategy, payload->>'reason' AS reason,
                 CAST(payload->>'switched' AS BOOLEAN) AS switched
          FROM v_events WHERE type = 'strategy_selected'
          QUALIFY row_number() OVER (PARTITION BY decision_id ORDER BY seq DESC) = 1),
emit AS (SELECT decision_id, sum(json_array_length(payload->'$.batch.intents')) AS n_intents,
                bool_and(CAST(payload->>'$.receipt.ok' AS BOOLEAN)) AS emit_ok
         FROM v_events WHERE type = 'intent_emitted' GROUP BY decision_id),
rej AS (SELECT decision_id, count(*) FILTER (WHERE disposition = 'rejected') AS n_rejected,
               count(*) FILTER (WHERE disposition = 'deferred') AS n_deferred
        FROM v_intents WHERE disposition IN ('rejected', 'deferred') GROUP BY decision_id),
ack AS (SELECT i.decision_id,
               string_agg(CAST(a.country AS VARCHAR) || ':' || a.status, ',' ORDER BY a.country) AS final_ack_status
        FROM (SELECT DISTINCT decision_id, batch_seq, country FROM v_intents WHERE disposition = 'emitted') i
        JOIN v_acks_final a ON a.batch_seq = i.batch_seq AND a.country = i.country
        GROUP BY i.decision_id),
outc AS (SELECT decision_id, arg_max(CAST(payload->>'reward' AS DOUBLE), seq) AS outcome_reward
         FROM v_events WHERE type = 'outcome' GROUP BY decision_id)
SELECT d.decision_id, obs.observation_date, bel.top_archetype, bel.max_posterior, bel.entropy,
       strat.strategy, strat.reason, strat.switched,
       coalesce(emit.n_intents, 0) AS n_intents, coalesce(rej.n_rejected, 0) AS n_rejected,
       coalesce(rej.n_deferred, 0) AS n_deferred,
       emit.emit_ok, ack.final_ack_status, outc.outcome_reward
FROM d LEFT JOIN obs USING (decision_id) LEFT JOIN bel USING (decision_id) LEFT JOIN strat USING (decision_id)
LEFT JOIN emit USING (decision_id) LEFT JOIN rej USING (decision_id) LEFT JOIN ack USING (decision_id)
LEFT JOIN outc USING (decision_id);

CREATE VIEW v_governor AS
SELECT seq, game_date, payload->>'state' AS state, payload->>'level' AS level,
       TRY_CAST(json_extract_string(payload, '$.metrics.avail_ram_bytes') AS DOUBLE) AS avail_ram_bytes,
       TRY_CAST(json_extract_string(payload, '$.metrics.fault_rate') AS DOUBLE) AS fault_rate,
       TRY_CAST(json_extract_string(payload, '$.metrics.spm') AS DOUBLE) AS spm,
       TRY_CAST(json_extract_string(payload, '$.metrics.spm_med6') AS DOUBLE) AS spm_med6,
       TRY_CAST(json_extract_string(payload, '$.metrics.spm_med24') AS DOUBLE) AS spm_med24,
       TRY_CAST(json_extract_string(payload, '$.metrics.parse_rss_peak_bytes') AS DOUBLE) AS parse_rss_peak_bytes,
       TRY_CAST(json_extract_string(payload, '$.metrics.parse_cpu_s') AS DOUBLE) AS parse_cpu_s,
       TRY_CAST(json_extract_string(payload, '$.metrics.parse_wall_s') AS DOUBLE) AS parse_wall_s,
       TRY_CAST(json_extract_string(payload, '$.metrics.saves_skipped') AS DOUBLE) AS saves_skipped,
       TRY_CAST(json_extract_string(payload, '$.metrics.workers_killed') AS DOUBLE) AS workers_killed
FROM v_events WHERE type = 'health' AND (payload->>'kind') = 'monthly';

CREATE VIEW v_deferred AS
SELECT seq, game_date, decision_id, payload->>'$.intent.intent_id' AS intent_id,
       payload->>'$.intent.kind' AS intent_kind, payload->>'rule' AS rule, payload->>'detail' AS detail
FROM v_events WHERE type = 'intent_rejected'
  AND ((payload->>'rule') = 'deferred' OR starts_with(coalesce(payload->>'detail', ''), 'deferred:'));

CREATE VIEW v_unemitted AS
SELECT seq, game_date, decision_id, payload->>'$.intent.intent_id' AS intent_id,
       CASE WHEN ((payload->>'rule') = 'deferred' OR starts_with(coalesce(payload->>'detail', ''), 'deferred:')) THEN 'deferred' ELSE 'rejected' END AS why,
       payload->>'rule' AS rule, payload->>'detail' AS detail
FROM v_events WHERE type = 'intent_rejected';
