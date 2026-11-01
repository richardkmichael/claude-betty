#!/bin/bash

# Extract everything before 'en_US' and trim trailing spaces to handle multi-word names
say -v '?' | grep "en_US" | sed 's/  *en_US.*//' | while read -r VOICE; do
    echo "Speaking with voice: $VOICE"
    
    # Use escaped double quotes for the AppleScript strings to handle names with spaces
    osascript -e "say \"Hello, This is voice $VOICE\" using \"$VOICE\""
done

