from collections import defaultdict

from flask import flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func

from app import db
from app.blueprints import bp
from app.forms import DialogueStatsForm
from app.models import PipelineSettings, Review, User
from app.services.stats import (
    build_graph_statistics_context,
    build_team_statistics,
    build_version_filter_options,
    format_version_label,
    get_rating_stats,
    get_user_stats,
    normalize_version_filter,
    select_rating_value,
)
from app.utils.context import admin_or_pm_required, get_base_context, get_current_user, login_required
from app.utils.teams import get_team_names


# Admin/PM statistics dashboard.
@bp.route("/admin/user-statistics", methods=["GET", "POST"])
@login_required
@admin_or_pm_required
def admin_user_statistics():
    """Build the full statistics dashboard context for admins and PMs."""
    stats_form = DialogueStatsForm(request.args)

    team_rows = db.session.query(User.team).filter(User.admin.is_(False)).distinct().all()
    valid_teams = set(get_team_names())
    team_values = [row[0] if row[0] in valid_teams else None for row in team_rows]
    team_choices = [("", "All Teams")]

    # Add a choice for users with no team
    if any(team is None for team in team_values):
        team_choices.append(("__none", "No Team"))
    team_choices.extend([(name, name) for name in sorted(valid_teams)])
    stats_form.team.choices = team_choices

    # Process the form submission
    if request.method == "POST":
        stats_form.validate_on_submit()

    # Determine the active tab
    selected_tab = request.args.get("tab") or request.form.get("tab") or "users"
    if selected_tab not in {"users", "graphs"}:
        selected_tab = "users"

    # Determine the graph view
    graph_view = request.args.get("graph_view") or request.form.get("graph_view") or "teams"
    if graph_view not in {"teams", "timeline", "quality", "pipeline"}:
        graph_view = "teams"

    user = get_current_user()
    user_stats = get_user_stats(user, stats_form)

    # The table shows a few different rating views, so I turn the query results
    # into user_id maps before merging them into each row.
    def to_rating_map(rows):
        return {
            row[0].id: {"avg_rating": row.avg_rating, "review_count": row.review_count}
            for row in rows
        }

    # Build maps of different rating types
    ratings_map = to_rating_map(get_rating_stats("author_latest"))
    ratings_all_map = to_rating_map(get_rating_stats("author_all"))
    reviewer_map = to_rating_map(get_rating_stats("reviewer"))

    # Version filters need a separate grouped query because a user's V1/V2/V3
    # averages can differ from their all-time rating.
    def version_avg_map(group_col):
        rows = (
            db.session.query(
                group_col.label("user_id"),
                Review.review_version.label("version"),
                func.avg(Review.rating).label("avg_rating"),
            )
            .filter(Review.rating.isnot(None))
            .group_by(group_col, Review.review_version)
            .all()
        )
        out = defaultdict(list)
        for row in rows:
            out[row.user_id].append(
                {"version": int(row.version or 1), "avg_rating": row.avg_rating}
            )
        return out
    
    # Build maps of average ratings by version for received and reviewer
    received_versions_map = version_avg_map(Review.original_author_id)
    reviewer_versions_map = version_avg_map(Review.reviewer_id)

    # Normalize a value to a float, or return None if it cannot be converted.
    def normalize_value(value):
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # Build a list of rating options for a user.
    def build_rating_options(all_time_value, version_entries):
        if all_time_value is None and not version_entries:
            return []
        options = [
            {
                "label": "All Time",
                "value": normalize_value(all_time_value),
                "is_default": True,
                "version": None,
            }
        ]   

        # Add options for each version
        for entry in sorted(version_entries, key=lambda item: item["version"]):
            options.append(
                {
                    "label": format_version_label(entry["version"]),
                    "value": normalize_value(entry["avg_rating"]),
                    "is_default": False,
                    "version": entry["version"],
                }
            )
        return options

    rated_version_numbers = sorted(
        {entry["version"] for entries in reviewer_versions_map.values() for entry in entries}
    )
    received_version_numbers = sorted(
        {entry["version"] for entries in received_versions_map.values() for entry in entries}
    )
    
    # Normalize the version filter values
    rated_version_filter = normalize_version_filter(request.args.get("avg_rated_version", "all"))
    rating_received_filter = normalize_version_filter(
        request.args.get("avg_received_version", "all")
    )

    # Process each user's statistics
    for stat in user_stats:
        rating_info = ratings_map.get(stat["user_id"])
        stat["avg_rating_received"] = rating_info["avg_rating"] if rating_info else None
        rating_all_info = ratings_all_map.get(stat["user_id"])
        stat["avg_rating_received_all_display"] = normalize_value(
            rating_all_info["avg_rating"] if rating_all_info else None
        )
        reviewer_info = reviewer_map.get(stat["user_id"])
        stat["avg_rated_reviews"] = reviewer_info["avg_rating"] if reviewer_info else None
        stat["avg_rating_received_options"] = build_rating_options(
            stat["avg_rating_received"], received_versions_map.get(stat["user_id"], [])
        )
        stat["avg_rated_reviews_options"] = build_rating_options(
            stat["avg_rated_reviews"], reviewer_versions_map.get(stat["user_id"], [])
        )
        stat["avg_rating_received_display"] = select_rating_value(
            stat["avg_rating_received_options"], rating_received_filter
        )
        stat["avg_rated_reviews_display"] = select_rating_value(
            stat["avg_rated_reviews_options"], rated_version_filter
        )

    totals = {
        "total_reviews": sum(stat["reviews_done"] for stat in user_stats),
        "total_dialogues": sum(stat["dialogues_created"] for stat in user_stats),
        "team_count": len({stat["team"] or "No Team" for stat in user_stats}),
    }

    # Pagination happens after filtering/enrichment so the totals still reflect
    # the full filtered result set, not just the current page.
    users_page = request.args.get("users_page", 1, type=int)

    # Ensure the page number is valid
    if users_page < 1:
        users_page = 1
    users_per_page = 20
    users_total = len(user_stats)
    users_pages = max(1, (users_total + users_per_page - 1) // users_per_page)

    # If the page number is greater than the total number of pages, set it to the last page
    if users_page > users_pages:
        users_page = users_pages
    users_start = (users_page - 1) * users_per_page
    users_end = users_start + users_per_page
    paged_user_stats = user_stats[users_start:users_end]

    # Build the query parameters for the user pages
    users_query_params = {
        "tab": "users",
        "name": stats_form.name.data or "",
        "team": stats_form.team.data or "",
        "version": stats_form.version.data or "",
        "start_date": stats_form.start_date.data.isoformat() if stats_form.start_date.data else "",
        "end_date": stats_form.end_date.data.isoformat() if stats_form.end_date.data else "",
        "avg_rated_version": rated_version_filter,
        "avg_received_version": rating_received_filter,
    }

    # Build the URL for a specific user page
    def users_page_url(page_number):
        return url_for(
            "main.admin_user_statistics",
            **users_query_params,
            users_page=page_number,
        )

    # Build the context for the graphs
    graph_context = build_graph_statistics_context()

    # Get the pipeline settings
    pipeline_settings = PipelineSettings.get_or_create()

    return render_template(
        "stats_dashboard.html",
        **get_base_context(),
        pipeline_settings=pipeline_settings,
        form=stats_form,
        user_stats=paged_user_stats,
        totals=totals,
        users_page=users_page,
        users_pages=users_pages,
        users_total=users_total,
        users_page_url=users_page_url,
        rated_review_header_options=build_version_filter_options(rated_version_numbers),
        rating_received_header_options=build_version_filter_options(received_version_numbers),
        rated_version_filter=rated_version_filter,
        rating_received_filter=rating_received_filter,
        selected_tab=selected_tab,
        graph_view=graph_view,
        initial_tab=graph_view if selected_tab == "graphs" else "users",
        **graph_context,
    )


@bp.route("/api/admin/pipeline-settings", methods=["POST"])
@login_required
def save_pipeline_settings():
    """Save admin-controlled settings used by the pipeline calculations."""
    from app.utils.context import get_current_user
    user = get_current_user()
    if not user.is_admin():
        return jsonify({"error": "Admin only"}), 403

    data = request.get_json(silent=True) or {}
    ps = PipelineSettings.get_or_create()

    # Validate and update the pipeline settings
    if "min_turns" in data:
        val = int(data["min_turns"])
        if 1 <= val <= 200:
            ps.min_turns = val
    if "min_reviews_required" in data:
        val = int(data["min_reviews_required"])
        if 1 <= val <= 10:
            ps.min_reviews_required = val
    if "rating_threshold" in data:
        val = float(data["rating_threshold"])
        if 1.0 <= val <= 5.0:
            ps.rating_threshold = val

    ps.updated_by_id = user.id
    db.session.commit()

    return jsonify({
        "ok": True,
        "min_turns": ps.min_turns,
        "min_reviews_required": ps.min_reviews_required,
        "rating_threshold": ps.rating_threshold,
    })


# Admin analytics graphs dashboard.
@bp.route("/graph_stats", methods=["GET", "POST"])
@login_required
def graph_stats():
    redirect_kwargs = request.args.to_dict(flat=True)
    if request.method == "POST":
        for key, value in request.form.items():
            if key == "csrf_token" or not value:
                continue
            redirect_kwargs[key] = value
    redirect_kwargs.setdefault("tab", "graphs")
    return redirect(url_for("main.admin_user_statistics", **redirect_kwargs))
