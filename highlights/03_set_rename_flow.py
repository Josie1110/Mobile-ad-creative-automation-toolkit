# ============================================================
# Highlight: Creative Set Manager — Safe Rename via Delete-and-Recreate
# Source: featured/creative_set_manager/MTG_Sets_Manager.py
#
# The problem:
#   Mintegral's open API does NOT support renaming a Creative Set
#   directly. When operators clone a campaign for a new game title,
#   they have to manually rebuild every Set with the new name —
#   often 30+ Sets per offer, each taking minutes.
#
#   The "obvious" workaround (delete then recreate) is dangerous:
#     - Forget to copy a field (geos, ad_outputs, HTML format flag)
#       and the recreated Set silently loses targeting or material.
#     - Crash between delete and create and the Set is gone.
#     - Some offers are at the 50-Set limit; create-then-delete fails.
#
# The solution:
#   1. Pre-flight: check the offer is under the 50-Set ceiling so
#      the temporary +1 won't bounce.
#   2. CREATE first with full state cloned (including the ENABLE
#      option per creative AND HTML-specific fields like creative_type
#      / format / material_type / file_type that are easy to drop).
#   3. DELETE the old only AFTER create returns 200.
#   4. If delete fails after create succeeds: log loud, don't roll back.
#      Reason: the new Set is already live and serving — destroying
#      it to "recover" would actually cause an outage. The user
#      cleans up the orphan old Set manually.
#
# Things to notice:
#   - Order matters: CREATE before DELETE, never the reverse
#   - HTML field preservation: silently dropping `creative_type`
#     turned playable ads into broken video stubs in early versions
#   - Asymmetric failure handling: success-then-failure is not
#     treated the same as failure-then-failure
# ============================================================

import time


def rename_creative_set_safely(api, offer_id, old_name, new_name, logger):
    """
    Returns: dict with success / error / api_response
    """
    # --- Pre-flight: Set-count safety ---------------------------------
    safety = api.check_creative_sets_count(offer_id)
    if not safety["success"]:
        return {"success": False, "error": f"Safety check failed: {safety.get('error')}"}
    if not safety["is_safe"]:
        return {"success": False,
                "error": f"Too many Sets ({safety['current_count']}/50) — rename would exceed cap"}

    logger.info(f"=== Rename: '{old_name}' -> '{new_name}' ===")

    # --- Step 1: Snapshot the original --------------------------------
    detail = api.get_creative_set_details(offer_id, old_name)
    if not detail["success"] or detail["data"].get("code") != 200:
        return {"success": False, "error": "Failed to fetch original Set details"}
    original = detail["data"].get("data", {})
    logger.info("Step 1: original config captured")

    # --- Step 2: Create new with full cloned state --------------------
    create_payload = {
        "offer_id": int(offer_id),
        "creative_set_name": new_name,
        "geos": original.get("geo", original.get("geos", [])),
        "ad_outputs": original.get("ad_outputs", []),
        "creatives": []
    }

    # Clone every creative — preserve HTML-specific fields that the
    # default mapping would drop. Losing creative_type silently turns
    # playable ads into broken video placeholders.
    for creative in original.get("creatives", []):
        cloned = {
            "creative_name": creative.get("creative_name", ""),
            "creative_md5": creative.get("creative_md5", ""),
            "option": creative.get("option", "ENABLE"),
        }
        for opt_field in ("creative_type", "format", "material_type", "file_type"):
            if opt_field in creative:
                cloned[opt_field] = creative[opt_field]
        create_payload["creatives"].append(cloned)

    create_result = api.make_request("POST", "creative_set", json=create_payload)
    if not create_result["success"] or create_result["data"].get("code") != 200:
        return {"success": False, "error": f"Create new Set failed: {create_result.get('error')}"}
    logger.info(f"Step 2: new Set '{new_name}' created")

    # --- Step 3: Delete the old (ONLY after create succeeded) ---------
    delete_result = api.delete_creative_set(offer_id, old_name)
    if not delete_result["success"]:
        # Asymmetric failure: new Set is already live and serving.
        # Do NOT roll back — that would cause a real outage.
        # Surface the orphan so the user can clean up manually.
        logger.warning(f"Old Set delete failed (orphan left): {delete_result.get('error')}")
        return {
            "success": False,
            "error": f"New Set created OK, but old Set delete failed: {delete_result.get('error')}",
            "api_response": {"create_success": True, "delete_success": False},
        }

    logger.info(f"Step 3: old Set '{old_name}' deleted — rename complete")
    return {"success": True, "error": None,
            "api_response": {"create_success": True, "delete_success": True}}
