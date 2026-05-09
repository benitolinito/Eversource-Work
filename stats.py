"""Utility functions for computing dialogue pipeline statistics."""

import json
from collections import defaultdict
from datetime import timedelta

from app.constants import MIN_REVIEWS_REQUIRED


# Load dialogue messages from JSON.
def _load_dialogue_messages(dialogue):
    try:
        messages = json.loads(dialogue.dialogue_json or "[]")
    except Exception:
        return None
    return messages if isinstance(messages, list) else None


def _is_meaningful_dialogue(dialogue, min_content_length=20):
    """Skip empty/test dialogues so the pipeline is based on real work."""
    messages = _load_dialogue_messages(dialogue)
    if messages is None:
        return False, None
    if len(messages) < min_content_length:
        return False, messages
    return True, messages


# Get the start of the week for a given day.
def _get_week_start(day):
    return day - timedelta(days=(day.weekday() + 1) % 7)


def compute_dialogue_pipeline(dialogues, min_content_length=20, min_reviews_required=2, rating_threshold=4.5):
    """Build the data behind the pipeline flowchart.

    The basic idea is: group dialogues into families, walk each family from V1
    through V2/V3, and count how far it got. I keep both cumulative counts
    (funnel progress) and unique counts (where each dialogue family stopped)
    because the UI can toggle between both views.
    """
    garbage = [] # Dialogues that are not meaningful enough to count in the pipeline.
    total_turns = 0 # Total number of turns in meaningful dialogues
    total_words = 0 # Total number of words in meaningful dialogues

    # Grouping by the root dialogue lets us treat V1, V2, and V3 as one
    # dialogue family instead of counting each version like a separate task.
    by_id = {} # Index of dialogues by id
    families = defaultdict(dict)

    # Process each dialogue
    for dialogue in dialogues:
        is_meaningful, messages = _is_meaningful_dialogue(
            dialogue, min_content_length=min_content_length
        )

        if not is_meaningful:
            garbage.append(dialogue)
            continue

        by_id[dialogue.id] = (dialogue, messages)

        # Count turns and words in the latest version of each dialogue
        if getattr(dialogue, "is_latest_version", False):
            total_turns += len(messages)
            for msg in messages:
                content = msg.get("content", "")
                if isinstance(content, str) and content:
                    total_words += len(content.split())

        root_id = dialogue.parent_dialogue_id or dialogue.id
        v = dialogue.version or 1
        families[root_id][v] = dialogue


    # Pipeline stage counters
    v1_in_progress = 0
    v1_completed = 0
    v1_review_1 = 0
    v1_review_2 = 0
    v1_rating_reached = 0
    v1_edit_completed = 0

    v2_review_1 = 0
    v2_review_2 = 0
    v2_rating_reached = 0
    v2_edit_completed = 0

    v3_review_1 = 0
    v3_review_2 = 0
    v3_rating_reached = 0
    rating_not_reached = 0

    v1_avg_ratings = []
    v2_avg_ratings = []
    v3_avg_ratings = []

    # stage_dialogues is cumulative, so a dialogue appears in every stage it
    # passed through. unique_stage_dialogues only stores the furthest stage it
    # reached, which powers the "Unique" toggle and bottleneck view.
    stage_dialogues = defaultdict(list)
    unique_stage_dialogues = defaultdict(list)
    
    # Constants
    RATING_THRESHOLD = rating_threshold
    MIN_REVIEWS = min_reviews_required

    def _avg_rating(dialogue):
        # LLM reviews are useful elsewhere, but this dashboard is meant to show
        # human review progress and quality.
        ratings = [r.rating for r in dialogue.reviews if r.rating is not None and not r.is_llm]
        return sum(ratings) / len(ratings) if ratings else None

    def _review_count(dialogue):
        return sum(1 for r in dialogue.reviews if not r.is_llm)

    # Process each dialogue family
    for root_id, versions in families.items():
        v1 = versions.get(1)
        if v1 is None:
            continue

        # Stage: V1 completed (not in progress)
        if v1.in_progress:
            v1_in_progress += 1
            continue
        v1_completed += 1
        stage_dialogues["v1_completed"].append(v1)
        furthest_stage = "v1_completed"
        furthest_dialogue = v1

        rc1 = _review_count(v1)
        avg1 = _avg_rating(v1)
        if rc1 >= 1:
            v1_review_1 += 1
            stage_dialogues["v1_review_1"].append(v1)
            furthest_stage = "v1_review_1"
        # Avg rating sidebar stat: always requires 2+ reviews
        if rc1 >= 2 and avg1 is not None:
            v1_avg_ratings.append(avg1)
        if rc1 >= MIN_REVIEWS:
            v1_review_2 += 1
            stage_dialogues["v1_review_2"].append(v1)
            furthest_stage = "v1_review_2"

            # Check if V1 already reached threshold — pipeline ends here
            if avg1 is not None and avg1 >= RATING_THRESHOLD:
                v1_rating_reached += 1
                stage_dialogues["rating_reached"].append(v1)
                unique_stage_dialogues["rating_reached"].append(v1)
                continue

        # V1 did not reach threshold — check for V2 (edit)
        v2 = versions.get(2)
        if v2 is None:
            unique_stage_dialogues[furthest_stage].append(furthest_dialogue)
            continue
        v1_edit_completed += 1
        stage_dialogues["v1_edit"].append(v2)
        furthest_stage = "v1_edit"
        furthest_dialogue = v2

        rc2 = _review_count(v2)
        avg2 = _avg_rating(v2)
        if rc2 >= 1:
            v2_review_1 += 1
            stage_dialogues["v2_review_1"].append(v2)
            furthest_stage = "v2_review_1"
        # Avg rating sidebar stat: always requires 2+ reviews
        if rc2 >= 2 and avg2 is not None:
            v2_avg_ratings.append(avg2)
        if rc2 >= MIN_REVIEWS:
            v2_review_2 += 1
            stage_dialogues["v2_review_2"].append(v2)
            furthest_stage = "v2_review_2"

            # Check if V2 reached threshold — pipeline ends here
            if avg2 is not None and avg2 >= RATING_THRESHOLD:
                v2_rating_reached += 1
                stage_dialogues["rating_reached"].append(v2)
                unique_stage_dialogues["rating_reached"].append(v2)
                continue

        # V2 did not reach threshold — check for V3 (edit)
        v3 = versions.get(3)
        if v3 is None:
            unique_stage_dialogues[furthest_stage].append(furthest_dialogue)
            continue
        v2_edit_completed += 1
        stage_dialogues["v2_edit"].append(v3)
        furthest_stage = "v2_edit"
        furthest_dialogue = v3

        rc3 = _review_count(v3)
        avg3 = _avg_rating(v3)
        if rc3 >= 1:
            v3_review_1 += 1
            stage_dialogues["v3_review_1"].append(v3)
            furthest_stage = "v3_review_1"
        # Avg rating sidebar stat: always requires 2+ reviews
        if rc3 >= 2 and avg3 is not None:
            v3_avg_ratings.append(avg3)
        if rc3 >= MIN_REVIEWS:
            v3_review_2 += 1
            stage_dialogues["v3_review_2"].append(v3)
            furthest_stage = "v3_review_2"

            # Check if V3 reached threshold -> pipeline ends here
            if avg3 is not None and avg3 >= RATING_THRESHOLD:
                v3_rating_reached += 1
                stage_dialogues["rating_reached"].append(v3)
                unique_stage_dialogues["rating_reached"].append(v3)

            # If V3 didn't reach the threshold, it's a failure
            else:
                rating_not_reached += 1
                stage_dialogues["rating_not_reached"].append(v3)
                unique_stage_dialogues["rating_not_reached"].append(v3)
            continue

        # V3 exists but doesn't have enough reviews yet
        unique_stage_dialogues[furthest_stage].append(furthest_dialogue)

    # Format average values
    def _fmt_avg(values):
        return f"{sum(values) / len(values):.2f}" if values else "\u2014"

    # Calculate percentage
    def _pct(part, whole):
        return round((part / whole) * 100, 1) if whole > 0 else 0

    total_dialogues = v1_completed # Total number of dialogues that have completed the first version

    categories = {
        "garbage": garbage,
    }
    
    # Calculate the total number of dialogues that reached the rating threshold
    rating_reached = v1_rating_reached + v2_rating_reached + v3_rating_reached


    # Calculate the total number of dialogues that did not reach the rating threshold
    pipeline_flow = {
        "total_turns": total_turns,
        "total_words": total_words,
        "total_dialogues": total_dialogues,
        "test_unmeaningful": len(garbage),
        "avg_rating_reviewed_twice": _fmt_avg(v1_avg_ratings),
        "avg_rating_v2_reviewed_twice": _fmt_avg(v2_avg_ratings),
        "avg_rating_v3_reviewed_twice": _fmt_avg(v3_avg_ratings),
        # Pipeline stages (cumulative)
        "in_progress": v1_in_progress,
        "v1_completed": v1_completed,
        "v1_review_1": v1_review_1,
        "v1_review_2": v1_review_2,
        "v1_rating_reached": v1_rating_reached,
        "v1_edit_completed": v1_edit_completed,
        "v2_review_1": v2_review_1,
        "v2_review_2": v2_review_2,
        "v2_rating_reached": v2_rating_reached,
        "v2_edit_completed": v2_edit_completed,
        "v3_review_1": v3_review_1,
        "v3_review_2": v3_review_2,
        "v3_rating_reached": v3_rating_reached,
        "rating_reached": rating_reached,
        "rating_reached_pct": _pct(rating_reached, total_dialogues),
        "rating_not_reached": rating_not_reached,
        "rating_not_reached_pct": _pct(rating_not_reached, total_dialogues),
        # Unique (stage-only) counts — dialogues whose furthest progress is this stage.
        # Derived from the unique_stage_dialogues lists so counts always match the
        # dialogues shown in the drill-down modal.
        "u_v1_completed": len(unique_stage_dialogues.get("v1_completed", [])),
        "u_v1_review_1": len(unique_stage_dialogues.get("v1_review_1", [])),
        "u_v1_review_2": len(unique_stage_dialogues.get("v1_review_2", [])),
        "u_v1_edit_completed": len(unique_stage_dialogues.get("v1_edit", [])),
        "u_v2_review_1": len(unique_stage_dialogues.get("v2_review_1", [])),
        "u_v2_review_2": len(unique_stage_dialogues.get("v2_review_2", [])),
        "u_v2_edit_completed": len(unique_stage_dialogues.get("v2_edit", [])),
        "u_v3_review_1": len(unique_stage_dialogues.get("v3_review_1", [])),
        "u_v3_review_2": len(unique_stage_dialogues.get("v3_review_2", [])),
    }

    return categories, pipeline_flow, stage_dialogues, unique_stage_dialogues


# calculate weekly v1 rating change/delta
def compute_weekly_v1_rating_trend(dialogues, min_content_length=20):
    """Return weekly average v1 rating and week-over-week percent change."""
    weekly_totals = defaultdict(float) # Total ratings for each week
    weekly_counts = defaultdict(int) # Number of ratings for each week

    # Iterate through each dialogue
    for dialogue in dialogues:
        is_meaningful, _messages = _is_meaningful_dialogue(
            dialogue, min_content_length=min_content_length
        )
        if not is_meaningful or getattr(dialogue, "version", 1) != 1:
            continue
        if len(getattr(dialogue, "reviews", [])) < MIN_REVIEWS_REQUIRED:
            continue
        
        # Calculate the weekly average rating for each dialogue
        for review in dialogue.reviews:
            rating = getattr(review, "rating", None)
            timestamp = getattr(review, "datetime_made", None)
            if rating is None or timestamp is None:
                continue
            week_start = _get_week_start(timestamp.date())
            weekly_totals[week_start] += float(rating)
            weekly_counts[week_start] += 1

    if not weekly_counts:
        return {
            "labels": [],
            "week_starts": [],
            "averages": [],
            "pct_changes": [],
            "latest_avg": None,
            "latest_pct_change": None,
            "latest_label": None,
        }

    first_week = min(weekly_counts)
    last_week = max(weekly_counts)

    labels = []
    week_starts = []
    averages = []
    pct_changes = []
    previous_avg = None

    current_week = first_week
    while current_week <= last_week:
        count = weekly_counts.get(current_week, 0)
        avg = (weekly_totals[current_week] / count) if count else None
        pct_change = None
        if avg is not None and previous_avg not in (None, 0):
            pct_change = ((avg - previous_avg) / previous_avg) * 100

        labels.append(current_week.strftime("%b %d"))
        week_starts.append(current_week.isoformat())
        averages.append(round(avg, 2) if avg is not None else None)
        pct_changes.append(round(pct_change, 2) if pct_change is not None else None)
        previous_avg = avg
        current_week += timedelta(days=7)

    latest_index = max(
        index for index, value in enumerate(averages) if value is not None
    )

    latest_abs_change = None
    if latest_index > 0:
        prev = averages[latest_index - 1]
        curr = averages[latest_index]
        if prev is not None and curr is not None:
            latest_abs_change = round(curr - prev, 2)

    # Return the weekly v1 rating trend
    return {
        "labels": labels,
        "week_starts": week_starts,
        "averages": averages,
        "pct_changes": pct_changes,
        "latest_avg": averages[latest_index],
        "latest_pct_change": pct_changes[latest_index],
        "latest_abs_change": latest_abs_change,
        "latest_label": labels[latest_index],
    }
