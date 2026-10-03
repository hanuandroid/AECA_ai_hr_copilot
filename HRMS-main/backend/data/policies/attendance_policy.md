<!--
DEMO / SEED DATA - synthetic HR policy for the CB Nest learning environment. Not a real company policy.
Sources of truth (must stay consistent): backend/app/api/v1/endpoints/attendance.py
(CLOCK_IN_CUTOFF = 09:00, HALF_DAY_MIN_SECONDS = 4.5 hours, modes PRESENT/WFH, APP_TIMEZONE).
Note: the legacy one-line seed text said "before 9:30 AM"; the application enforces 9:00 AM, so this document follows the code.
-->
# Attendance Policy

## Clocking In

Employees record attendance in CB Nest by clocking in once per working day from the Attendance page.

When you clock in, choose your work mode: Present (working from the office) or WFH (working from home).

All attendance times use the company time zone (India Standard Time).

## Late Arrival

The standard clock-in time is 9:00 AM.

If you clock in after 9:00 AM, your attendance for that day is recorded as Late. Clocking in at or before 9:00 AM is recorded as On Time.

Late arrivals are shown in your attendance history on the Attendance page.

## Clocking Out and Half-Day Attendance

Clock out once at the end of your working day. You must clock in before you can clock out.

If the time between your clock-in and clock-out is less than 4.5 hours, the day is recorded as Half Day attendance.

Half Day attendance is an attendance record, not a leave request. If you plan to work only half a day, apply for half-day leave under the Leave Policy.

## Work From Home Attendance

On days you work from home, clock in with the WFH work mode. Work from home requires prior approval from your reporting manager under the Work From Home Policy.
