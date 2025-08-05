#!/bin/bash

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting application setup...${NC}"

# 1. Check if virtual environment exists, create if not
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Virtual environment not found. Creating venv...${NC}"
    python3 -m venv .venv
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}Virtual environment created successfully.${NC}"
    else
        echo -e "${RED}Failed to create virtual environment.${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}Virtual environment already exists.${NC}"
fi

# 2. Activate the virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
source .venv/bin/activate

# Check if activation was successful
if [ "$VIRTUAL_ENV" != "" ]; then
    echo -e "${GREEN}Virtual environment activated: $VIRTUAL_ENV${NC}"
else
    echo -e "${RED}Failed to activate virtual environment.${NC}"
    exit 1
fi

# 3. Install requirements
echo -e "${YELLOW}Installing requirements...${NC}"
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}Requirements installed successfully.${NC}"
    else
        echo -e "${RED}Failed to install requirements.${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}No requirements.txt found. Skipping package installation.${NC}"
fi

# 4. Run the main application
echo -e "${YELLOW}Starting the application...${NC}"
if [ -f "main.py" ]; then
    python main.py
else
    echo -e "${RED}main.py not found in the project root.${NC}"
    exit 1
fi