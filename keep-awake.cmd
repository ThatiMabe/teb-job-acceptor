@echo off
rem Double-click this to stop THIS PC going to sleep while it is plugged in.
rem Sleeping stops every check: jobs that arrive while the PC sleeps are missed.
rem It only changes "sleep when plugged in" to Never. The screen can still switch off. Nothing else is changed.
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
echo.
echo Done: this PC will no longer sleep while plugged in. Keep it plugged in.
echo To undo:  powercfg /change standby-timeout-ac 15
echo.
pause
