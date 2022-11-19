# NexusAutoDL
This tool is designed for automating the process of downloading mods from [Nexusmods](https://www.nexusmods.com/), without being a premium member. It is designed with mod managers such as [Portmod](https://gitlab.com/portmod/portmod) and [Wabbajack](https://www.wabbajack.org/). It also contains an integration for Nexusmods own mod manager, [Vortex](https://www.nexusmods.com/about/vortex/), which contrary to the affore mentioned, does not automatically open the mods download page but instead forces users to suffer through another click. This tool is designed to automatically click through a download list and download all of the contained mods without user intervention.

# Features 
This tool offers a plethora of different features. It is designed to work with multiple screens and even has a browser integration. If you’re working with multiple monitors and are also using Vortex, this tool has the ability to open and move your primary browser and your Vortex instance, so that you can start downloading right away. In addition, this tool offers Interruption Detection. As Vortex sometimes throws errors or otherwise asks the user for input. These interruptions will be detected and circumvented.

# Prerequisites
First you will need to have Python installed.

Then when running the script either on one monitor, or with --force-primary enabled, as well as --vortex enabled, you will need to make sure that both the Vortex Mod Manager window and your Browser window are visible at the same time.


# Running the Script
Clone this repository:

`git clone https://github.com/jaylann/NexusAutoDL`

Or manually download the repository.

Then go into the directory you cloned/downloaded to.

`cd NexusAutoDL`

Install all necessary packages.

`pip install -r requirements.txt`

Run python script with or without arguments.

Windows:
`python main.py <arguments>`

MacOS/Linux:
`python3 main.py <arguments>`



# Command Line Options
- `--browser <browserName>: selects browser to open and move to work
with Vortex. Can only be used in combination with --vortex. Currently
supported browsers: “chrome”, “firefox”`
- `--vortex: specifies use with Vortex mod manager`
- `--verbose: prints verbose output`
- `--force-primary: forces a system with multiple monitors to only be scanned on it’s primary display`

# Credit
Credit goes to [nexus-autodl](https://github.com/parsiad/nexus-autodl) for inspiring this project.
