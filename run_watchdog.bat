@echo off
chcp 65001 > nul
title BalatroLLM Watchdog Supervisor
echo ========================================================
echo  🃏 BalatroLLM Autonomous Agent with Watchdog Supervisor
echo ========================================================
python watchdog.py %*
pause
