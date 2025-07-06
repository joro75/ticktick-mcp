import datetime
import functools
import json
import logging
from typing import Any, Dict, List, Optional

from .client import TickTickClientSingleton

TaskObject = Dict[str, Any]

# Define the ToolLogicError exception
class ToolLogicError(Exception):
    pass

# --- Helper Function --- #
def format_response(result: Any) -> str:
    """Formats the result from ticktick-py into a JSON string for MCP."""
    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, indent=2, default=str)
        except TypeError as e:
            logging.error(f"Failed to serialize response object: {e} - Object: {result}", exc_info=True)
            return json.dumps({"error": "Failed to serialize response", "details": str(e)})
    elif result is None:
         return json.dumps(None)
    else:
        logging.warning(f"Formatting unexpected type: {type(result)} - Value: {result}")
        return json.dumps({"result": str(result)})

# --- Decorator for Client Check --- #
def require_ticktick_client(func):
    """Decorator to check if ticktick_client is initialized before calling the tool."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # Get the client instance using the singleton's getter method
        ticktick_client = TickTickClientSingleton.get_client()
        if not ticktick_client:
            logging.error("TickTick client is not initialized. Cannot execute tool.")
            # Consider how to communicate this back to the MCP framework/user
            # Maybe raise a specific exception or return an error structure
            # For now, returning an error message in a dict format similar to tool outputs
            return json.dumps({"error": "TickTick client not initialized. Please check credentials and restart."})
        # If client exists, proceed with the original function call
        # Original function will now get the client via TickTickClientSingleton.get_client() itself
        return await func(*args, **kwargs)
    return wrapper

# --- Internal Helper to Get All Tasks --- #
def _get_all_tasks_from_ticktick() -> List[TaskObject]:
    """Internal helper to fetch all *uncompleted* tasks from all projects."""
    if not TickTickClientSingleton.get_client():
        logging.error("_get_all_tasks_from_ticktick called when client is not initialized.")
        raise ConnectionError("TickTick client not initialized.")

    TickTickClientSingleton.get_client().sync()
    all_tasks = []
    try:
        projects_state = TickTickClientSingleton.get_client().state.get('projects', [])
    except Exception as e:
        logging.error(f"Error accessing client state for projects: {e}", exc_info=True)
        projects_state = []

    # Get unique project IDs from state, add inbox ID
    project_ids = {p.get('id') for p in projects_state if p.get('id')}
    try:
        if TickTickClientSingleton.get_client().inbox_id:
            project_ids.add(TickTickClientSingleton.get_client().inbox_id)
    except Exception as e:
        logging.error(f"Error accessing client inbox_id: {e}", exc_info=True)

    logging.debug(f"Fetching uncompleted tasks from {len(project_ids)} projects...")
    for project_id in project_ids:
        try:
            # get_from_project fetches *uncompleted* tasks for a project
            tasks_in_project = TickTickClientSingleton.get_client().task.get_from_project(project_id)
            if tasks_in_project:
                 if isinstance(tasks_in_project, list):
                     all_tasks.extend(tasks_in_project)
                 elif isinstance(tasks_in_project, dict):
                     all_tasks.append(tasks_in_project)
                 else:
                    logging.warning(f"Unexpected data type received from get_from_project for {project_id}: {type(tasks_in_project)}")
        except Exception as e:
            logging.warning(f"Failed to get tasks for project {project_id}: {e}")

    logging.info(f"Found {len(all_tasks)} total uncompleted tasks.")
    return all_tasks

# --- Helper for Due Date Parsing --- #
def _parse_due_date(due_date_str: Optional[str]) -> Optional[datetime.date]:
    """Parses TickTick's dueDate string (e.g., '2024-07-27T...') into a date object."""
    if not due_date_str or not isinstance(due_date_str, str):
        return None
    try:
        # Extract YYYY-MM-DD part.
        if len(due_date_str) >= 10:
            date_part = due_date_str[:10]
            return datetime.datetime.strptime(date_part, "%Y-%m-%d").date()
        else:
            logging.warning(f"dueDate string too short to parse: {due_date_str}")
            return None
    except (ValueError, TypeError) as e:
        logging.warning(f"Could not parse dueDate string '{due_date_str}': {e}")
        return None 

# --- Helper for reducing the amount of data returned of a task --- #
def _filter_unneeded_properties_tasks(tasks: List[Dict]) -> List[Dict]:
    """
    Filters out unneeded properties from each task dictionary in the input list.

    The function performs the following cleanups on each task:
    - Removes integer properties with zero values from a predefined list.
    - Removes certain properties always, regardless of their value.
    - Removes certain properties if they are empty (e.g., empty lists, empty strings).
    - Removes the 'kind' property if its value is the default "TEXT".
    - Removes the 'isAllDay' property if it is False.
    - Removes 'repeatTaskId' if it is equal to the task's own 'id'.
    - Removes 'timeZone' if not date or time related property is present anymore

    Args:
        tasks (List[Dict]): A list of task dictionaries to be filtered.

    Returns:
        List[Dict]: A new list of task dictionaries with unneeded properties removed.
    """
    filtered = []

    # Loop over the tasks and remove unneeded information
    for task in tasks:
        # Make a copy to avoid mutating the original task dict
        task = task.copy()

        # Remove zero value integer properties
        zeroValueProps = ["deleted", "imgMode", "priority", "progress", "status"]
        for prop in zeroValueProps:
            if prop in task and task.get(prop, 0) == 0:
                del task[prop]

        # Remove some properties always
        remove_props = [
            "columnId", "commentCount", "completedUserId", "creator", "createdTime", "etag",
            "focusSummaries", "isFloating", "modifiedTime", "pomodoroSummaries", "repeatFirstData",
            "repeatFrom", "sortOrder"
        ]
        for prop in remove_props:
            if prop in task:
                del task[prop]

        # Remove some properties when they are empty
        remove_empty_props = [
            "attachments", "childIds", "desc", "exDate",
            "items", "reminder", "reminders", "repeatFlag", "tags"
        ]
        for prop in remove_empty_props:
            if prop in task and not task[prop]:
                del task[prop]

        # Remove the default 'kind' property if it is the default "TEXT"
        if task.get("kind") == "TEXT":
            del task["kind"]

        # Remove 'isAllDay' if False
        if task.get("isAllDay") is False:
            del task["isAllDay"]

        # Remove 'repeatTaskId' if it equals the task's own 'id'
        if "repeatTaskId" in task and task.get("id") == task.get("repeatTaskId"):
            del task["repeatTaskId"]

        # Remove 'timeZone' if no date related properties are present anymore
        if "timeZone" in task:
            remove_timeZone = True
            date_props = [ "startDate", "dueDate", "repeatFirstDate" ]
            for prop in date_props:
                if prop in task:
                    remove_timeZone = False
                    break
            if remove_timeZone:
                del task["timeZone"]

        filtered.append(task)

    return filtered


# --- Helper for reducing the amount of data returned of a project --- #
def _filter_unneeded_properties_projects(projects: List[Dict]) -> List[Dict]:
    """
    Filters out unneeded properties from each project dictionary in the input list.

    The function performs the following cleanups on each task:
    - Removes integer properties with zero values from a predefined list.
    - Removes certain properties always, regardless of their value.
    - Removes certain properties if they are empty (e.g., empty lists, empty strings).
    - Removes the 'kind' property if its value is the default "TASK"

    Args:
        projects (List[Dict]): A list of projects dictionaries to be filtered.

    Returns:
        List[Dict]: A new list of project dictionaries with unneeded properties removed.
    """
    filtered = []

    # Loop over the projects and remove unneeded information
    for prj in projects:
        # Make a copy to avoid mutating the original project dict
        prj = prj.copy()

        # Remove zero value integer properties
        zeroValueProps = [ ]
        for prop in zeroValueProps:
            if prop in prj and prj.get(prop, 0) == 0:
                del prj[prop]

        # Remove some properties always
        remove_props = [
            "barcodeNeedAudit", "closed", "etag", "inAll", "modifiedTime", "muted", "needAudit",
            "openToTeam", "permission", "reminderType", "source", "sortOption", "sortOrder",
            "showType", "sortType", "teamMemberPermission", "timeline", "transferred",
            "userCount", "viewMode"
        ]
        for prop in remove_props:
            if prop in prj:
                del prj[prop]

        # Remove some properties when they are empty
        remove_empty_props = [
            "color", "groupId", "notificationOptions", "teamId"
        ]
        for prop in remove_empty_props:
            if prop in prj and not prj[prop]:
                del prj[prop]

        # Remove the default 'kind' property if it is the default "TASK"
        if prj.get("kind") == "TASK":
            del prj["kind"]

        # Remove the default 'kind' property if it is the default "TASK"
        if prj.get("isOwner") == True:
            del prj["isOwner"]

        filtered.append(prj)

    return filtered
