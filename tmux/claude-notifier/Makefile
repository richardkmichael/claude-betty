# Build claude-notifier.app: terminal-notifier with the Claude icon, shown as "Claude Code".
#
#   make -C tmux/claude-notifier install
#
# macOS takes a notification's icon and name from the app that posts it, so a custom icon needs a
# custom copy of terminal-notifier.  terminal-notifier is a submodule pinned to a release, built
# with its own `make icon`.  Building needs only the Command Line Tools (xcode-select --install).
#
# The first notification the installed app posts asks for notification permission.

APP_NAME     := claude-notifier
DISPLAY_NAME := Claude Code
ICON         := $(CURDIR)/ClaudeIcon-Rounded.png
UPSTREAM     := terminal-notifier
APP          := $(UPSTREAM)/build/$(APP_NAME).app
INFO_PLIST   := $(APP)/Contents/Info.plist
INSTALL_DIR  ?= $(HOME)/Applications
INSTALLED    := $(INSTALL_DIR)/$(APP_NAME).app
CORE_SERVICES := /System/Library/Frameworks/CoreServices.framework/Frameworks
LSREGISTER   := $(CORE_SERVICES)/LaunchServices.framework/Support/lsregister

.DEFAULT_GOAL := app

.PHONY: app
app: $(UPSTREAM)/Makefile
	$(MAKE) -C $(UPSTREAM) icon ICON='$(ICON)' APP_NAME=$(APP_NAME)
	/usr/libexec/PlistBuddy -c 'Set :CFBundleName $(DISPLAY_NAME)' '$(INFO_PLIST)'
	/usr/libexec/PlistBuddy -c 'Add :CFBundleDisplayName string $(DISPLAY_NAME)' '$(INFO_PLIST)'
	codesign --force --sign - '$(APP)'

# A clone without --recurse-submodules leaves the submodule empty.  Fetch only the
# pinned commit.
$(UPSTREAM)/Makefile:
	git submodule update --init --depth 1 -- $(UPSTREAM)

# Registering the app with Launch Services makes macOS read its new name and icon.
.PHONY: install
install: app
	mkdir -p '$(INSTALL_DIR)'
	rm -rf '$(INSTALLED)'
	cp -R '$(APP)' '$(INSTALL_DIR)/'
	$(LSREGISTER) -f '$(INSTALLED)'
	@echo 'Installed $(INSTALLED)'
	@echo 'Test it, and allow notifications when macOS asks:'
	@echo "  '$(INSTALLED)/Contents/MacOS/terminal-notifier' -title Test -message 'It works'"

.PHONY: clean
clean:
	rm -rf '$(UPSTREAM)/build'
