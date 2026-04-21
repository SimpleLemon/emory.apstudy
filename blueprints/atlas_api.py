# In blueprints/atlas_api.py (simplified)
from services.atlas_client import get_subjects

@atlas_bp.route("/subjects")
@login_required
def list_subjects():
    term = request.args.get("term", "Fall_2026")
    result = get_subjects(term)
    if "error" in result:
        return jsonify(result), 400 if "Invalid" in result["error"] else 404
    return jsonify(result)