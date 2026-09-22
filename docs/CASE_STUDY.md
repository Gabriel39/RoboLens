# Case study: from a failed grasp to similar scenes and training candidates

[English](CASE_STUDY.md) | [简体中文](CASE_STUDY.zh-CN.md)

Suppose a robot drops an object while lifting it from a table. An engineer saves images around the
failure and wants to know: have similar conditions occurred before? Which objects or phases account
for most failures? Which clips should be considered for the next training round?
This is a hypothetical use case, not a claim about any DROID-100 episode's outcome.

## Prepare the data

The export pipeline writes states, actions, videos, and metadata from 100 episodes to S3 / OSS Lance.
SigLIP2 produces 768-dimensional vectors for sampled frames, followed by cosine vector indexes.
The default per-frame export has 96,636 image vectors and preserves all three video views.
DROID-100 is useful for validating the workflow; it is not a massive dataset itself and does not
guarantee enough failures of the particular grasp being investigated.

State/action are seven-dimensional source records. This version only names motor_0 through motor_6;
do not interpret a component as gripper opening or a specific joint angle without verification.
`next.reward` and `next.done` cannot directly label a complete grasp failure either.

## 1. Find similar scenes

Pass the incident image to `doris.prepare_query --image`, or select the sample-id of an exported
failure keyframe. The former runs pinned SigLIP2; the latter reads its stored vector. Doris
`vector_search()` uses the Lance index to retrieve similar frames, deduplicates by episode, and
keeps up to 50 review candidates.

Filter to the same camera view and model revision before retrieval. Do not filter to failures only:
retain successful attempts under similar conditions as comparisons. Visual similarity does not
prove equivalent action sequences or failure causes; reviewing video is still necessary.

## 2. Join video and actions, then review a complete attempt

`sample_id = episode:camera:frame_index` identifies a frame embedding. `media_id = episode:camera`
links its original video. `(episode_index, frame_index)` links state/action at the same time step.
`episode_index` links task and episode metadata. All three cameras share one set of state/action time steps.

The query provides the key moment and an initial time window. Extract the MP4, then manually label
the start and end of the complete attempt. Distinguish, for example, no contact, slipping after
contact, and dropping during transport. Mark uncertain outcomes as unknown. Without reliable
review labels, the workflow can provide similar candidates but cannot claim a failure-cause analysis.

## 3. Analyze failure distributions with Doris

The SQL first reports retrieved, reviewed, and usable known-outcome counts, then groups by object and scene:

`Failure rate = reviewed, usable failed attempts / reviewed, usable attempts with known outcomes`

Further grouping by failure_stage and failure_reason identifies clusters worth investigating.
This cohort was selected by similarity rather than randomly sampled from deployed traffic. Its
failure rate is not the deployment-wide rate, and label statistics alone cannot establish causation.
No fabricated percentages are shown when real labels are unavailable.

## 4. Select clips worth considering for training

Candidates must be reviewed, task-matched, usable in quality, within valid time bounds, and covered
by reliable split and training-use records. The same collection scene/session must not span training
and evaluation sets. Previously trained samples are excluded. Within each object/scene/training-use
bucket, deduplicate by scene group and cap the count to avoid filling the added set with near duplicates.

Successful clips from similar conditions can become behavior-cloning positives. Failures can support
failure classification, value functions, or human diagnosis. Learning recovery actions additionally
requires locating successful recovery intervals; the current query does not annotate them automatically.
“Worth training on” means rule-selected candidates here. Actual gains require subsequent training and
independent evaluation.

## Outputs and execution guides

The workflow produces a similar-scene review list, label coverage and failure distributions, and
training candidates with time intervals and intended uses. See the [Doris guide](../doris/README.md)
for commands, the [pipeline guide](../pipeline/README.md) for ingestion and processing, and
[the schema reference](SCHEMA.md) for tables and join keys.
