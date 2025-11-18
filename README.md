# YoutubeCommunityScraper | yp-dl
yp-dl is an asynchronous scraper for downloading Youtube Community posts in json format.

![](media/example.gif)

## Motivation
Youtube stops retrieving old community posts after 200 posts on a channel. There's no way to access/view older posts if you do not have the link to them or their ID. 

## Installation

```bash
pip install yp-dl
```

## Features
- [x] Asynchronous support
- [x] For every post it retrieves:
  - [x] post_link
  - [x] time_since
  - [x] utc_timestamp at download
  - [x] video_link
  - [x] image_links
  - [x] text_content
  - [ ] poll_content
- [x] Update support for the json files when new posts are made
- [x] Progress visualization during download

## Usage
```
usage: yp-dl [-h] [-f FOLDER_PATH] [-r] [-u] [-v] [-l N] [-o] [-d] link [link ...]

An asynchronous scraper that downloads youtube posts from youtube channels in json format.

positional arguments:
  link                  Provide any number of links. 
                        Link example: https://www.youtube.com/@3blue1brown

options:
  -h, --help            show this help message and exit
  -f FOLDER_PATH, --folder-path FOLDER_PATH
                        Provide the path of the folder you wish to store/update your json files. 
                        If it's in the current working directory (CWD), just type the folder 
                        name. If none is provided, everything will be stored/updated in the CWD.
  -r, --reverse         Reverses the order of the posts from oldest first to newest first. 
                        Be wary though, if you use this option with --update, your post order 
                        will be messed up.
  -u, --update          Appends the existing json file(s) with the new posts.
  -v, --verbose         Gives more details about what's going on when the program runs.
  -l N, --limit N       Stops scraping after collecting N posts (from newest). 
  -o, --overwrite-cookie
                        Overwrites the SOCS cookie in the cookies.txt file with a Default SOCS 
                        cookie within the project. Use if having problems retrieving posts.
  -d, --delete-cookie   Removes the cookie file to generate it again. Use if your SOCS key 
                        has expired (lifetime is 2 years).
```
