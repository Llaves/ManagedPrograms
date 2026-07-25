#!/usr/bin/env python3
"""
3_month_calendar.py

Generate a 3-month calendar grid on a LEGAL sheet (8.5" x 14") in PORTRAIT,
black & white. Accepts an ICS file and places events into the day cells.

Usage:
    python 3_month_calendar.py YEAR START_MONTH ICS_PATH [--out OUTFILE] [--highlight-tag TAG] [--highlight-color COLOR]

Examples:
    # Default: highlight events ending with "#r
    python 3_month_calendar.py 2026 5 calendar.ics
    
    # Custom tag: highlight events ending with "#vacation"
    python 3_month_calendar.py 2026 5 calendar.ics --highlight-tag "#vacation"
    
    # Custom color: use a very pale blue instead of red
    python 3_month_calendar.py 2026 5 calendar.ics --highlight-tag "#work" --highlight-color "lightblue"
"""

from __future__ import annotations
from gooey import Gooey, GooeyParser

import sys
import argparse
import calendar
import re
import os
from datetime import datetime, date, time, timedelta, tzinfo
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple, Union

from icalendar import Calendar
from icalendar.cal import Component
from dateutil.rrule import rrulestr, rruleset
from dateutil.tz import tzlocal, gettz
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.colors import Color

# ---------------------------------------------------------
# Layout Constants
# ---------------------------------------------------------
# Page dimensions (Legal size, portrait)
PAGE_WIDTH: float = 8.5 * inch
PAGE_HEIGHT: float = 14 * inch
PAGE_MARGIN: float = 0.5 * inch

# Font names
FONT_BOLD: str = "Helvetica-Bold"
FONT_NORMAL: str = "Helvetica"

# Font sizes (points)
MONTH_TITLE_FONT_SIZE: int = 16
WEEKDAY_FONT_SIZE: int = 9
DAY_NUMBER_FONT_SIZE: int = 9
EVENT_FONT_SIZE: float = 6.5
EVENT_LINE_LEADING: float = EVENT_FONT_SIZE + 2

# Section heights
MONTH_HEADER_HEIGHT: float = 0.40 * inch
WEEKDAY_ROW_HEIGHT: float = 0.18 * inch

# Cell padding (points)
CELL_PAD_LEFT: int = 4
CELL_PAD_RIGHT: int = 4
CELL_PAD_TOP: int = 10
CELL_PAD_BOTTOM: int = 4
DAY_NUMBER_OFFSET_X: int = 4
DAY_NUMBER_OFFSET_Y: int = 12

# Weekday labels
WEEKDAY_LABELS: List[str] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

# Highlight background inset
HIGHLIGHT_BG_INSET: float = 0.5  # Points to inset the background from the border

# Predefined colors
COLOR_MAP = {
    'red': Color(red=1.0, green=0.85, blue=0.85, alpha=1.0),      # Very pale red
    'blue': Color(red=0.85, green=0.85, blue=1.0, alpha=1.0),     # Very pale blue
    'green': Color(red=0.85, green=1.0, blue=0.85, alpha=1.0),    # Very pale green
    'yellow': Color(red=1.0, green=1.0, blue=0.85, alpha=1.0),    # Very pale yellow
    'pink': Color(red=1.0, green=0.85, blue=0.95, alpha=1.0),     # Very pale pink
    'cyan': Color(red=0.85, green=1.0, blue=1.0, alpha=1.0),      # Very pale cyan
    'gray': Color(red=0.5, green=0.5, blue=0.5, alpha=1.0),          # Very pale gray
}


def parse_color(color_name: str) -> Color:
    """Parse a color name or RGB string into a Color object."""
    # Check predefined colors
    if color_name.lower() in COLOR_MAP:
        return COLOR_MAP[color_name.lower()]
    
    # Try to parse as "r,g,b" where each is 0-255
    if ',' in color_name:
        try:
            parts = color_name.split(',')
            if len(parts) == 3:
                r = int(parts[0].strip()) / 255.0
                g = int(parts[1].strip()) / 255.0
                b = int(parts[2].strip()) / 255.0
                # Make pale (mix with white)
                pale_factor = 0.85
                r = 1.0 - (1.0 - r) * pale_factor
                g = 1.0 - (1.0 - g) * pale_factor
                b = 1.0 - (1.0 - b) * pale_factor
                return Color(red=r, green=g, blue=b, alpha=1.0)
        except (ValueError, IndexError):
            pass
    
    # Default to light red
    print(f"Warning: Unknown color '{color_name}', using light red")
    return COLOR_MAP['red']


# ---------------------------------------------------------
# Data Structures
# ---------------------------------------------------------
@dataclass
class CalEvent:
    """Represents a calendar event."""
    start: datetime
    end: Optional[Union[date, datetime]]
    all_day: bool
    title: str           # Clean title (without highlight tag)
    raw_title: str       # Original title (with tag)
    uid: str
    is_recurring: bool = False
    should_highlight: bool = False


@dataclass
class MonthData:
    """Holds layout data for a single month in the calendar grid."""
    year: int
    month: int
    weeks: List[List[int]]
    num_rows: int


# ---------------------------------------------------------
# Tag Processing
# ---------------------------------------------------------
def extract_tag_from_title(title: str, highlight_tag: str) -> Tuple[str, bool]:
    """
    Extract a tag from the end of an event title.
    
    Args:
        title: The event title.
        highlight_tag: The tag to look for (e.g., "#highlight").
    
    Returns:
        Tuple of (cleaned_title, has_tag).
    """
    if not highlight_tag:
        return title, False
    
    # Look for the tag at the end of the string (possibly with whitespace)
    pattern = rf'{re.escape(highlight_tag)}\s*$'
    if re.search(pattern, title):
        # Remove the tag and any trailing whitespace
        cleaned = re.sub(pattern, '', title).rstrip()
        return cleaned, True
    
    return title, False


# ---------------------------------------------------------
# Date Helpers
# ---------------------------------------------------------
def month_year_after(year: int, month: int, delta_months: int) -> Tuple[int, int]:
    """
    Calculate the year and month after adding delta_months.
    
    Args:
        year: The starting year.
        month: The starting month (1-12).
        delta_months: Number of months to add.
    
    Returns:
        Tuple of (new_year, new_month).
    """
    m = month - 1 + delta_months
    new_year = year + m // 12
    new_month = m % 12 + 1
    return new_year, new_month


def last_day_of_month(year: int, month: int) -> int:
    """
    Get the last day of the specified month.
    
    Args:
        year: The year.
        month: The month (1-12).
    
    Returns:
        The last day of the month (28-31).
    """
    return calendar.monthrange(year, month)[1]


# ---------------------------------------------------------
# ICS Parsing - Helper Functions
# ---------------------------------------------------------
def extract_calendar_timezone(cal: Calendar, default_tz: Optional[tzinfo] = None) -> tzinfo:
    """
    Extract the timezone from a calendar, or return a default.
    
    Args:
        cal: The parsed iCalendar object.
        default_tz: Fallback timezone if none found in calendar.
    
    Returns:
        The calendar's timezone or the default.
    """
    for comp in cal.walk():
        if comp.name == "VTIMEZONE":
            tzid = comp.get("TZID")
            if tzid:
                tz = gettz(str(tzid))
                if tz:
                    return tz
    return default_tz or tzlocal()


def collect_recurrence_exceptions(cal: Calendar) -> Dict[str, Set[date]]:
    """
    Collect all RECURRENCE-ID exceptions from a calendar.
    
    These represent modified instances of recurring events that should
    be excluded from normal recurrence expansion.
    
    Args:
        cal: The parsed iCalendar object.
    
    Returns:
        Dictionary mapping event UID to set of exception dates.
    """
    exceptions: Dict[str, Set[date]] = defaultdict(set)
    
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        
        uid = str(component.get("UID", ""))
        recurrence_id = component.get("RECURRENCE-ID")
        
        if recurrence_id:
            try:
                rid_dt = component.decoded("RECURRENCE-ID")
                if isinstance(rid_dt, datetime):
                    exceptions[uid].add(rid_dt.date())
                elif isinstance(rid_dt, date):
                    exceptions[uid].add(rid_dt)
            except (KeyError, ValueError, TypeError) as e:
                print(f"Warning: Could not parse RECURRENCE-ID for {uid}: {e}")
    
    return exceptions


def collect_exdate_dates(component: Component) -> Set[date]:
    exdates: Set[date] = set()
    exdate_prop = component.get("EXDATE")

    if not exdate_prop:
        return exdates

    def add_exdate(value):
        if isinstance(value, datetime):
            exdates.add(value.date())
        elif isinstance(value, date):
            exdates.add(value)

    items = exdate_prop if isinstance(exdate_prop, list) else [exdate_prop]

    for item in items:
        if hasattr(item, "dts"):
            for dt in item.dts:
                add_exdate(dt.dt if hasattr(dt, "dt") else dt)
        else:
            add_exdate(item)

    return exdates


def normalize_datetime(
    dt: Optional[Union[date, datetime]], 
    cal_tz: tzinfo
) -> Optional[Union[date, datetime]]:
    """
    Normalize a datetime to be timezone-aware.
    
    Args:
        dt: The date or datetime to normalize.
        cal_tz: The calendar timezone to apply if dt is naive.
    
    Returns:
        The normalized datetime, or None if input was None.
    """
    if dt is None:
        return None
    # All-day events (date objects) are returned as-is
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt
    # Make naive datetimes timezone-aware
    if dt.tzinfo is None:
        return dt.replace(tzinfo=cal_tz)
    return dt


def parse_rrule_until_date(rrule_str: str) -> Tuple[str, Optional[date]]:
    """
    Parse and extract the UNTIL date from an RRULE string.
    
    Args:
        rrule_str: The raw RRULE string.
    
    Returns:
        Tuple of (cleaned_rrule_string, until_date or None).
    """
    parts = rrule_str.split(';')
    cleaned_parts = []
    until_date = None
    
    for part in parts:
        if part.startswith('UNTIL='):
            until_val = part.split('=', 1)[1]
            try:
                if len(until_val) >= 8:
                    until_date = datetime.strptime(until_val[:8], '%Y%m%d').date()
            except ValueError:
                pass  # Keep until_date as None if parsing fails
        else:
            cleaned_parts.append(part)
    
    return ';'.join(cleaned_parts), until_date


def expand_recurrence(
    component: Component,
    dtstart: Union[date, datetime],
    cal_tz: tzinfo,
    span_start_dt: datetime,
    span_end_dt: datetime,
    uid: str,
    exdates: Set[date]
) -> List[datetime]:
    """
    Expand recurring event occurrences within a date span.
    
    Args:
        component: The VEVENT component.
        dtstart: The event start date/datetime.
        cal_tz: The calendar timezone.
        span_start_dt: Start of the date range to expand.
        span_end_dt: End of the date range to expand.
        uid: Event UID for error messages.
        exdates: Set of dates to exclude from recurrence.
    
    Returns:
        List of occurrence datetimes.
    """
    occurrences: List[datetime] = []
    rrule_prop = component.get("RRULE")
    
    if not rrule_prop:
        return occurrences
    
    try:
        # Prepare DTSTART anchor as aware datetime
        if isinstance(dtstart, date) and not isinstance(dtstart, datetime):
            rrule_anchor = datetime.combine(dtstart, time.min).replace(tzinfo=cal_tz)
        else:
            rrule_anchor = dtstart
        
        # Parse RRULE string
        rrule_raw = rrule_prop.to_ical()
        rrule_ical = rrule_raw.decode() if isinstance(rrule_raw, bytes) else rrule_raw
        rrule_str = rrule_ical.split(":", 1)[1] if ":" in rrule_ical else rrule_ical
        
        # Extract and handle UNTIL date separately
        rrule_cleaned, manual_until = parse_rrule_until_date(rrule_str)
        
        # Build rruleset and expand
        rs = rruleset()
        rr = rrulestr(rrule_cleaned, dtstart=rrule_anchor)
        rs.rrule(rr)
        
        # Add EXDATEs
        for exdate in exdates:
            exdate_dt = datetime.combine(exdate, time.min).replace(tzinfo=cal_tz)
            rs.exdate(exdate_dt)
        
        raw_occs = rs.between(
            span_start_dt - timedelta(days=1),
            span_end_dt + timedelta(days=1),
            inc=True
        )
        
        # Filter by manual UNTIL date if present
        if manual_until:
            raw_occs = [
                occ for occ in raw_occs 
                if (occ.date() if isinstance(occ, datetime) else occ) <= manual_until
            ]
        
        # Normalize occurrences
        for occ in raw_occs:
            if isinstance(occ, datetime):
                occ_date = occ.date()
            elif isinstance(occ, date):
                occ_date = occ
            else:
                continue
            
            if isinstance(dtstart, date) and not isinstance(dtstart, datetime):
                # All-day event
                occ_local = datetime.combine(occ_date, time.min).replace(tzinfo=cal_tz)
            else:
                # Timed event
                occ_local = occ if occ.tzinfo else occ.replace(tzinfo=cal_tz)
            
            occurrences.append(occ_local)
            
    except (ValueError, TypeError, AttributeError) as e:
        print(f"Warning: Failed to parse RRULE for {uid}: {e}")
    
    return occurrences


def get_event_end_date(
    component: Component,
    dtstart: Union[date, datetime]
) -> Optional[Union[date, datetime]]:
    """
    Get the end date/datetime of an event.
    
    Args:
        component: The VEVENT component.
        dtstart: The event start date/datetime.
    
    Returns:
        The event end, or None if not specified.
    """
    if component.get("DTEND") is not None:
        return component.decoded("DTEND")
    
    if component.get("DURATION") is not None:
        try:
            duration = component.get("DURATION").dt
            return dtstart + duration
        except (AttributeError, TypeError):
            return None
    
    return None


def add_event_to_dates(
    events_by_date: Dict[date, List[CalEvent]],
    occ_dt: datetime,
    dtstart: Union[date, datetime],
    dtend_t: Optional[Union[date, datetime]],
    dtstart_t: Optional[Union[date, datetime]],
    summary: str,
    raw_summary: str,
    uid: str,
    span_start_date: date,
    span_end_date: date,
    is_recurring: bool = False,
    should_highlight: bool = False
) -> None:
    """
    Add an event occurrence to the events_by_date dictionary.

    Multi-day events are added to each date they span.

    Args:
        events_by_date: Dictionary to populate.
        occ_dt: The occurrence datetime.
        dtstart: Original event start.
        dtend_t: Normalized event end.
        dtstart_t: Normalized event start.
        summary: Cleaned event title.
        raw_summary: Original event title (with tag).
        uid: Event UID.
        span_start_date: Start of visible date range.
        span_end_date: End of visible date range.
        is_recurring: Whether this is from a recurring event.
        should_highlight: Whether this event should trigger highlighting.
    """
    is_all_day = isinstance(dtstart, date) and not isinstance(dtstart, datetime)
    start_d = occ_dt.date()

    # Calculate end date
    if dtend_t is None:
        end_d = start_d
    elif isinstance(dtend_t, date) and not isinstance(dtend_t, datetime):
        offset_days = (dtend_t - dtstart_t).days
        end_d = start_d + timedelta(days=offset_days)
    else:
        offset_td = dtend_t - dtstart_t
        end_d = (occ_dt + offset_td).date()

    # Calculate inclusive display end date
    if dtend_t is None:
        display_end_d = start_d
    elif is_all_day:
        offset_days = (dtend_t - dtstart_t).days
        # DTEND is exclusive for all-day events
        display_end_d = start_d + timedelta(days=offset_days - 1)
    else:
        offset_td = dtend_t - dtstart_t
        display_end_d = (occ_dt + offset_td).date()

    # Create event object
    ev = CalEvent(
        start=occ_dt,
        end=display_end_d if display_end_d > start_d else None,
        all_day=is_all_day,
        title=summary,
        raw_title=raw_summary,
        uid=uid,
        is_recurring=is_recurring,
        should_highlight=should_highlight
    )

    # Add to each date in the span (inclusive)
    cur = start_d
    while cur <= display_end_d:
        if span_start_date <= cur <= span_end_date:
            events_by_date[cur].append(ev)
        cur += timedelta(days=1)


# ---------------------------------------------------------
# ICS Parsing - Main Function
# ---------------------------------------------------------
def load_events_from_ics(
    path: str,
    span_start_date: date,
    span_end_date: date,
    highlight_tag: str,
    default_tz: Optional[tzinfo] = None
) -> Dict[date, List[CalEvent]]:
    """
    Load and parse events from an ICS file.
    
    Expands single events and RRULE/RDATE/EXDATE occurrences that fall 
    within the specified date span. Multi-day events are added to each 
    date they span.
    
    Args:
        path: Path to the ICS file.
        span_start_date: Start of the date range to include.
        span_end_date: End of the date range to include.
        highlight_tag: Tag to look for in event titles (e.g., "#highlight").
        default_tz: Default timezone if not specified in calendar.
    
    Returns:
        Dictionary mapping dates to lists of CalEvent objects.
    
    Raises:
        FileNotFoundError: If the ICS file doesn't exist.
        ValueError: If the ICS file is malformed.
    """
    with open(path, "rb") as f:
        cal = Calendar.from_ical(f.read())
    
    cal_tz = extract_calendar_timezone(cal, default_tz)
    recurrence_exceptions = collect_recurrence_exceptions(cal)
    events_by_date: Dict[date, List[CalEvent]] = defaultdict(list)
    
    span_start_dt = datetime.combine(span_start_date, time.min).replace(tzinfo=cal_tz)
    span_end_dt = datetime.combine(span_end_date, time.max).replace(tzinfo=cal_tz)
    
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        
        # Skip cancelled events
        status = component.get('STATUS')
        if status and str(status).upper() == 'CANCELLED':
            continue
        
        uid = str(component.get("UID", ""))
        raw_summary = str(component.get("SUMMARY", ""))
        
        # Extract tag and clean title
        clean_summary, has_tag = extract_tag_from_title(raw_summary, highlight_tag)
        
        # Parse start time
        try:
            dtstart = component.decoded("DTSTART")
        except (KeyError, ValueError) as e:
            print(f"Warning: Could not parse DTSTART for event: {e}")
            continue
        
        dtend = get_event_end_date(component, dtstart)
        dtstart_t = normalize_datetime(dtstart, cal_tz)
        dtend_t = normalize_datetime(dtend, cal_tz)
        
        # Collect EXDATEs for this specific event
        exdates = collect_exdate_dates(component)
        
        # Check if this is a recurring event
        rrule_prop = component.get("RRULE")
        is_recurring = rrule_prop is not None
        
        # Expand occurrences
        if rrule_prop:
            occurrences = expand_recurrence(
                component, dtstart, cal_tz, 
                span_start_dt, span_end_dt, uid, exdates
            )
        else:
            # Non-recurring event
            if isinstance(dtstart_t, date) and not isinstance(dtstart_t, datetime):
                occ = datetime.combine(dtstart_t, time.min).replace(tzinfo=cal_tz)
            else:
                occ = dtstart_t
            occurrences = [occ] if occ else []
        
        # Add each occurrence to the date map
        for occ_dt in occurrences:
            # Skip if this occurrence is in the recurrence exceptions (RECURRENCE-ID)
            occ_date = occ_dt.date()
            if occ_date in recurrence_exceptions.get(uid, set()):
                continue
            
            add_event_to_dates(
                events_by_date, occ_dt, dtstart, dtend_t, dtstart_t,
                clean_summary, raw_summary, uid, span_start_date, span_end_date,
                is_recurring, has_tag
            )
    
    # Sort events within each date
    for d in events_by_date:
        events_by_date[d].sort(key=lambda e: (not e.all_day, e.start))
    
    return events_by_date


# ---------------------------------------------------------
# Text Wrapping
# ---------------------------------------------------------
def wrap_text_to_lines(
    text: str, 
    fontname: str, 
    fontsize: float, 
    max_width: float
) -> List[str]:
    """
    Wrap text to fit within a maximum width.
    
    Args:
        text: The text to wrap.
        fontname: The font name for width calculation.
        fontsize: The font size for width calculation.
        max_width: Maximum line width in points.
    
    Returns:
        List of wrapped lines.
    """
    words = text.split()
    if not words:
        return [""]
    
    lines: List[str] = []
    current_line = words[0]
    
    for word in words[1:]:
        trial = f"{current_line} {word}"
        if pdfmetrics.stringWidth(trial, fontname, fontsize) <= max_width:
            current_line = trial
        else:
            lines.append(current_line)
            current_line = word
    
    lines.append(current_line)
    return lines


# ---------------------------------------------------------
# PDF Generation - Helper Functions
# ---------------------------------------------------------
def calculate_month_layout(
    year: int, 
    start_month: int
) -> Tuple[List[MonthData], int]:
    """
    Calculate layout data for three consecutive months.
    
    Args:
        year: The starting year.
        start_month: The starting month (1-12).
    
    Returns:
        Tuple of (list of MonthData, total row count).
    """
    total_rows = 0
    month_data: List[MonthData] = []
    
    for m_index in range(3):
        myear, mmon = month_year_after(year, start_month, m_index)
        cal_obj = calendar.Calendar(firstweekday=6)  # Sunday first
        weeks = cal_obj.monthdayscalendar(myear, mmon)
        used_weeks = [w for w in weeks if any(day != 0 for day in w)]
        num_rows = len(used_weeks) if used_weeks else 1
        total_rows += num_rows
        
        month_data.append(MonthData(
            year=myear,
            month=mmon,
            weeks=used_weeks,
            num_rows=num_rows
        ))
    
    return month_data, total_rows


def format_event_text(ev: CalEvent) -> str:
    """
    Format an event for display in a calendar cell.
    
    Args:
        ev: The calendar event.
    
    Returns:
        Formatted string for display (using cleaned title without tag).
    """
    if ev.all_day:
        return ev.title or ""
    
    try:
        local_start = ev.start.astimezone(tzlocal())
        timestr = local_start.strftime("%-I:%M %p")
    except ValueError:
        local_start = ev.start.astimezone(tzlocal())
        timestr = local_start.strftime("%I:%M %p").lstrip('0')
    
    return f"{timestr} {ev.title or ''}"


def draw_month_header(
    c: canvas.Canvas,
    mdata: MonthData,
    box_x: float,
    box_y: float,
    box_w: float,
    box_h: float
) -> None:
    """
    Draw the month title and weekday headers.
    
    Args:
        c: The PDF canvas.
        mdata: Month layout data.
        box_x: Left edge of the month box.
        box_y: Bottom edge of the month box.
        box_w: Width of the month box.
        box_h: Height of the month box.
    """
    # Month title
    title_y = box_y + box_h - (MONTH_HEADER_HEIGHT * 0.5)
    c.setFont(FONT_BOLD, MONTH_TITLE_FONT_SIZE)
    c.drawCentredString(
        box_x + box_w / 2,
        title_y - (MONTH_TITLE_FONT_SIZE / 4),
        f"{calendar.month_name[mdata.month]} {mdata.year}"
    )
    
    # Weekday headers
    cell_w = box_w / 7
    c.setFont(FONT_NORMAL, WEEKDAY_FONT_SIZE)
    weekday_y = box_y + box_h - MONTH_HEADER_HEIGHT - (WEEKDAY_ROW_HEIGHT * 0.5) + 2
    
    for wd in range(7):
        x = box_x + wd * cell_w
        c.drawCentredString(
            x + cell_w / 2,
            weekday_y - (WEEKDAY_FONT_SIZE / 4),
            WEEKDAY_LABELS[wd]
        )


def draw_highlight(
    c: canvas.Canvas,
    x: float,
    y: float,
    cell_w: float,
    row_height: float,
    highlight_type: str,
    highlight_color: Color
) -> None:
    """
    Draw a cell highlight for events.
    
    Args:
        c: The PDF canvas.
        x: Left edge of the cell.
        y: Bottom edge of the cell.
        cell_w: Width of the cell.
        row_height: Height of the cell.
        highlight_type: 'start' for right half, 'end' for left half, 'middle' for full.
        highlight_color: The color to use for highlighting.
    """
    c.setFillColor(highlight_color)
    
    if highlight_type == 'start':
        # Highlight right half of cell (for first day of multi-day event)
        c.rect(
            x + cell_w / 2 + HIGHLIGHT_BG_INSET,
            y + HIGHLIGHT_BG_INSET,
            cell_w / 2 - 2 * HIGHLIGHT_BG_INSET,
            row_height - 2 * HIGHLIGHT_BG_INSET,
            stroke=0,
            fill=1
        )
    elif highlight_type == 'end':
        # Highlight left half of cell (for last day of multi-day event)
        c.rect(
            x + HIGHLIGHT_BG_INSET,
            y + HIGHLIGHT_BG_INSET,
            cell_w / 2 - 2 * HIGHLIGHT_BG_INSET,
            row_height - 2 * HIGHLIGHT_BG_INSET,
            stroke=0,
            fill=1
        )
    elif highlight_type == 'middle':
        # Draw full cell highlight (slightly inset)
        c.rect(
            x + HIGHLIGHT_BG_INSET, 
            y + HIGHLIGHT_BG_INSET, 
            cell_w - 2 * HIGHLIGHT_BG_INSET, 
            row_height - 2 * HIGHLIGHT_BG_INSET, 
            stroke=0, 
            fill=1
        )
    
    c.setFillColorRGB(0, 0, 0)  # Reset to black


def should_highlight_cell(
    evs: List[CalEvent],
    check_date: date
) -> str:
    """
    Determine if and how to highlight a cell based on tagged events.

    Args:
        evs: List of events for this day.
        check_date: The date to check.

    Returns:
        'start' for first day (right half highlight),
        'end' for last day (left half highlight),
        'middle' for middle/single day (full highlight),
        '' for no highlight.
    """
    # Pick the longest tagged event covering this day
    best_ev = None
    best_duration = 0

    for ev in evs:
        if not ev.should_highlight:
            continue

        # ev.end is None for single-day events, otherwise the inclusive end date
        if ev.end is None:
            duration = 1
        else:
            duration = (ev.end - ev.start.date()).days + 1

        if duration > best_duration:
            best_duration = duration
            best_ev = ev

    if not best_ev:
        return ''

    start_date = best_ev.start.date()
    end_date = best_ev.end or start_date  # single-day events have end == None

    # Single-day event → full cell
    if start_date == end_date:
        return 'middle'

    # Multi-day event
    if check_date == start_date:
        return 'start'
    elif check_date == end_date:
        return 'end'
    else:
        return 'middle'


def draw_cell_events(
    c: canvas.Canvas,
    evs: List[CalEvent],
    frame_x: float,
    frame_y: float,
    frame_w: float,
    frame_h: float
) -> None:
    """
    Draw events within a day cell.
    
    Args:
        c: The PDF canvas.
        evs: List of events for this day.
        frame_x: Left edge of the event frame.
        frame_y: Bottom edge of the event frame.
        frame_w: Width of the event frame.
        frame_h: Height of the event frame.
    """
    remaining_h = frame_h
    drawn_count = 0
    
    for ev_idx, ev in enumerate(evs):
        raw = format_event_text(ev)
        lines = wrap_text_to_lines(raw, FONT_NORMAL, EVENT_FONT_SIZE, frame_w)
        needed_h = len(lines) * EVENT_LINE_LEADING
        
        if needed_h <= remaining_h:
            c.setFont(FONT_NORMAL, EVENT_FONT_SIZE)
            yline = frame_y + remaining_h - EVENT_LINE_LEADING
            for ln in lines:
                c.drawString(frame_x, yline, ln)
                yline -= EVENT_LINE_LEADING
            remaining_h -= needed_h
            drawn_count += 1
        else:
            break
    
    # Show "+N more" if events were truncated
    leftover = len(evs) - drawn_count
    if leftover > 0 and EVENT_LINE_LEADING <= remaining_h:
        c.setFont(FONT_NORMAL, EVENT_FONT_SIZE)
        c.drawString(frame_x, frame_y + 2, f"+{leftover} more")


# ---------------------------------------------------------
# PDF Generation - Main Function
# ---------------------------------------------------------
def draw_three_months_pdf(
    year: int,
    start_month: int,
    events_by_date: Dict[date, List[CalEvent]],
    out_path: str,
    highlight_color: Color
) -> None:
    """
    Generate a 3-month calendar PDF.
    
    Args:
        year: The starting year.
        start_month: The starting month (1-12).
        events_by_date: Dictionary mapping dates to event lists.
        out_path: Output PDF file path.
        highlight_color: Color to use for highlighted cells.
    
    Raises:
        IOError: If the file cannot be written.
    """
    c = canvas.Canvas(out_path, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    
    usable_w = PAGE_WIDTH - 2 * PAGE_MARGIN
    usable_h = PAGE_HEIGHT - 2 * PAGE_MARGIN
    
    # Calculate layout
    month_data, total_rows = calculate_month_layout(year, start_month)
    total_grid_space = usable_h - 3 * (MONTH_HEADER_HEIGHT + WEEKDAY_ROW_HEIGHT)
    row_height = total_grid_space / total_rows
    cell_w = usable_w / 7
    
    current_y = PAGE_HEIGHT - PAGE_MARGIN
    
    # Iterate over the three months with an index
    for month_idx, mdata in enumerate(month_data):
        month_height = MONTH_HEADER_HEIGHT + WEEKDAY_ROW_HEIGHT + (mdata.num_rows * row_height)
        
        box_x = PAGE_MARGIN
        box_y = current_y - month_height
        box_w = usable_w
        box_h = month_height
        
        # Draw headers
        draw_month_header(c, mdata, box_x, box_y, box_w, box_h)
        
        # Compute previous month info for filling in leading zeros in first week
        first_day_weekday = date(mdata.year, mdata.month, 1).weekday()  # 0=Mon, 6=Sun
        num_prev = (first_day_weekday + 1) % 7  # days from previous month
        
        # Compute previous month's year and month
        if mdata.month == 1:
            prev_year = mdata.year - 1
            prev_month = 12
        else:
            prev_year = mdata.year
            prev_month = mdata.month - 1
        prev_last_day = last_day_of_month(prev_year, prev_month)
        
        # FIRST PASS: Draw background highlights (so they don't cover borders)
        for row_idx in range(mdata.num_rows):
            week = mdata.weeks[row_idx]
            
            for col in range(7):
                x = box_x + col * cell_w
                grid_top_y = box_y + box_h - MONTH_HEADER_HEIGHT - WEEKDAY_ROW_HEIGHT
                y = grid_top_y - (row_idx + 1) * row_height
                
                daynum = week[col]
                if daynum == 0:
                    # Only show previous-month days in the first month
                    if month_idx == 0 and row_idx == 0 and col < num_prev:
                        prev_day = prev_last_day - (num_prev - 1 - col)
                        actual_date = date(prev_year, prev_month, prev_day)
                        evs = events_by_date.get(actual_date, [])
                        highlight_type = should_highlight_cell(evs, actual_date)
                        if highlight_type:
                            draw_highlight(c, x, y, cell_w, row_height, highlight_type, highlight_color)
                    continue
                
                this_date = date(mdata.year, mdata.month, daynum)
                evs = events_by_date.get(this_date, [])
                highlight_type = should_highlight_cell(evs, this_date)
                if highlight_type:
                    draw_highlight(c, x, y, cell_w, row_height, highlight_type, highlight_color)
        
        # SECOND PASS: Draw grid lines and content
        for row_idx in range(mdata.num_rows):
            week = mdata.weeks[row_idx]
            
            for col in range(7):
                x = box_x + col * cell_w
                grid_top_y = box_y + box_h - MONTH_HEADER_HEIGHT - WEEKDAY_ROW_HEIGHT
                y = grid_top_y - (row_idx + 1) * row_height
                
                # Draw cell border
                c.rect(x, y, cell_w, row_height, stroke=1, fill=0)
                
                daynum = week[col]
                if daynum == 0:
                    # Only show previous-month days in the first month
                    if month_idx == 0 and row_idx == 0 and col < num_prev:
                        prev_day = prev_last_day - (num_prev - 1 - col)
                        actual_date = date(prev_year, prev_month, prev_day)
                        # Draw day number in gray
                        c.setFillColor(parse_color('gray'))
                        c.setFont(FONT_NORMAL, DAY_NUMBER_FONT_SIZE)
                        c.drawString(
                            x + DAY_NUMBER_OFFSET_X, 
                            y + row_height - DAY_NUMBER_OFFSET_Y, 
                            str(prev_day)
                        )
                        c.setFillColorRGB(0, 0, 0)  # Reset to black
                        # Draw events for that date
                        evs = events_by_date.get(actual_date, [])
                        if evs:
                            frame_x = x + CELL_PAD_LEFT
                            frame_y = y + CELL_PAD_BOTTOM
                            frame_w = cell_w - (CELL_PAD_LEFT + CELL_PAD_RIGHT)
                            frame_h = row_height - (CELL_PAD_TOP + CELL_PAD_BOTTOM)
                            draw_cell_events(c, evs, frame_x, frame_y, frame_w, frame_h)
                    # If not first month, leave cell empty (only border drawn)
                    continue
                
                # Current month day
                c.setFont(FONT_NORMAL, DAY_NUMBER_FONT_SIZE)
                c.drawString(
                    x + DAY_NUMBER_OFFSET_X, 
                    y + row_height - DAY_NUMBER_OFFSET_Y, 
                    str(daynum)
                )
                
                # Events for current month day
                this_date = date(mdata.year, mdata.month, daynum)
                evs = events_by_date.get(this_date, [])
                if evs:
                    frame_x = x + CELL_PAD_LEFT
                    frame_y = y + CELL_PAD_BOTTOM
                    frame_w = cell_w - (CELL_PAD_LEFT + CELL_PAD_RIGHT)
                    frame_h = row_height - (CELL_PAD_TOP + CELL_PAD_BOTTOM)
                    draw_cell_events(c, evs, frame_x, frame_y, frame_w, frame_h)
        
        current_y = box_y
    
    c.showPage()
    c.save()
    print(f"Wrote: {out_path}")
# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------
@Gooey(
    program_name="3-Month Calendar Generator",
    default_size=(500, 650),  # (width, height)
)
def main() -> None:
    """Main GUI/CLI entry point."""
    parser = GooeyParser(
        description="Generate a 3-month calendar PDF from an ICS file."
    )
    parser.add_argument(
        "year",
        type=int,
        default=datetime.now().year,
        help="Starting year"
    )
    parser.add_argument(
        "start_month",
        type=int,
        choices=range(1, 13),
        default=datetime.now().month,
        metavar="START_MONTH",
        help="Starting month (1-12)"
    )
    parser.add_argument(
        "ics_path",
        help="Path to the ICS calendar file",
        widget="FileChooser",
        gooey_options={
            "wildcard": "Calendar files (*.ics;*.ical;*.cal)|*.ics;*.ical;*.cal|All files (*.*)|*.*"
        }
    )
    parser.add_argument(
        "--out", "-o",
        default=None,
        help="Output PDF filename"
    )
    parser.add_argument(
        "--highlight-tag", "-t",
        default="#r",
        help="Tag to look for in event titles"
    )
    parser.add_argument(
        "--highlight-color", "-c",
        default="pink",
        help="Color for highlighting"
    )

    args = parser.parse_args()

    # ---- everything that used to be in main() past parse_args ----
    year = args.year
    start_month = args.start_month
    ics_path = args.ics_path
    out_path = args.out
    highlight_tag = args.highlight_tag
    highlight_color = parse_color(args.highlight_color)

    first_day_weekday = date(year, start_month, 1).weekday()
    num_prev_first = (first_day_weekday + 1) % 7
    span_start = date(year, start_month, 1) - timedelta(days=num_prev_first)

    end_year, end_month = month_year_after(year, start_month, 2)
    span_end = date(end_year, end_month, last_day_of_month(end_year, end_month))

    input_dir = os.path.dirname(os.path.abspath(ics_path))

    if out_path is None:
        default_name = f"{year}_{start_month:02d}_calendar.pdf"
        out_path = os.path.join(input_dir, default_name)
    else:
        if os.path.dirname(out_path) == "":
            out_path = os.path.join(input_dir, out_path)
        base, ext = os.path.splitext(out_path)
        if not ext:
            out_path += ".pdf"

    try:
        events = load_events_from_ics(
            ics_path, span_start, span_end, highlight_tag, default_tz=tzlocal()
        )
        draw_three_months_pdf(year, start_month, events, out_path, highlight_color)
    except FileNotFoundError:
        print(f"\n🛑 ERROR: ICS file not found: {ics_path}")
        sys.exit(1)
    except PermissionError as e:
        print(f"\n🛑 ERROR: Permission denied: {e}")
        sys.exit(1)
        
    except IOError as e:
        print(f"\n🛑 ERROR: Could not write to: {out_path}")
        print(f"Details: {e}")
        print("The file may be open in another application.")
        sys.exit(1)
        
    except ValueError as e:
        print(f"\n🛑 ERROR: Invalid data: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()