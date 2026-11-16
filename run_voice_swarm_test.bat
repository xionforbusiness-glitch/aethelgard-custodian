@echo off
title Aethelgard Voice Swarm Tester
cd /d "C:\Users\omara\Desktop\aethelgard-custodian"
echo ==============================================================================
echo 🐝 Launching Aethelgard Voice Swarm QA Tester (LIVE GEMINI 3.8 FLASH)
echo ==============================================================================
python -u tests\swarm_voice_tester.py --mode headless --live-llm --save-all-audio
echo.
echo ==============================================================================
echo 📄 Check tests\reports\ for the Markdown audit report.
echo 🎙️ Check tests\recordings\ for the .wav audio clips.
echo ==============================================================================
pause
