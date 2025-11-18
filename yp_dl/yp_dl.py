import itertools
from requests_html import HTMLSession, AsyncHTMLSession, HTMLResponse
from lxml import etree
import re
import json
import logging
import sys
import asyncio
import argparse
import os
import urllib.parse
from rich.progress import Progress, TextColumn, TimeElapsedColumn, SpinnerColumn
from rich.logging import RichHandler
from datetime import datetime, timezone
from yp_dl.exceptions import BadCookie

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIE_PATH = os.path.join(ROOT_DIR, "cookies.txt")
DEFAULT_SOCS_COOKIE = "CAESEwgDEgk2NDg4NTY2OTgaAnJvIAEaBgiAtae0Bg"

logging.basicConfig(format='%(message)s', level=logging.WARNING, handlers=[RichHandler(rich_tracebacks=True)])


def _handle_cookie_file(mode: str, cookie: str | None = None) -> str | None:
    with open(COOKIE_PATH, mode=mode, encoding="utf-8") as target:
        if mode == 'r':
            return target.readline()
        elif mode == 'w':
            target.write(cookie)

    return None


def get_SOCS_cookie() -> str:
    # Look for the SOCS cookie in the cookies.txt file
    try:
        cookie = _handle_cookie_file('r')
    except FileNotFoundError:
        logging.debug("cookies.txt doesn't exist")
        session = HTMLSession()
        result = session.get(url='https://www.youtube.com/feed')

        # Try to get cookie after filling the consent form
        try:
            form = result.html.xpath('/html/body/c-wiz/div/div/div/div[2]/div[1]/div[3]/div[1]/form[1]')[0].html
            root = etree.HTML(form)

            elements = root.xpath('//input')
            data = {element.attrib['name']: element.attrib['value'] for element in elements}

            result = session.post(url="https://consent.youtube.com/save", data=data)
            cookie = session.cookies['SOCS']
            _handle_cookie_file('w', cookie=cookie)

        # Try to get SOCS cookie if no consent form is present
        except Exception as error:
            logging.debug(error)
            try:
                cookie = session.cookies['SOCS']
                if cookie == "CAAaBgiAtae0Bg" or len(cookie) <= 14:
                    raise BadCookie("Invalid SOCS cookie")
                _handle_cookie_file('w', cookie=cookie)

             # If all fails, return default cookie
            except KeyError:
                logging.debug("Unable to get cookie even after skipping form")
                return DEFAULT_SOCS_COOKIE
            except BadCookie as error:
                logging.debug(error)
                return DEFAULT_SOCS_COOKIE

    return cookie


def _payload(token: str, originalURL: str) -> dict:
    json_payload = {
        "context": {
            "client": {
                "userAgent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36,gzip(gfe)",
                "clientName": "WEB",
                "clientVersion": "2.20231010.10.01",
                "originalUrl": originalURL,
                "platform": "DESKTOP",
                "browserName": "Chrome",
                "browserVersion": "139.0.0.0",
                "acceptHeader": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "utcOffsetMinutes": 0
            }
        },
        "continuation": token
    }
    return json_payload


def _get_video_link(post: dict) -> str | None:
    try:
        return 'https://www.youtube.com/watch?v=' + post['backstageAttachment']['videoRenderer']['videoId']
    except KeyError as error:
        logging.debug(f'[POST_ID: {post["postId"]}] function: _get_video_link: KeyError {error}')
        return None


def _handle_multi_images(post: dict) -> list[str] | None:
    try:
        links = list()
        images = post['backstageAttachment']['postMultiImageRenderer']['images']
        for image in images:
            links.append(image['backstageImageRenderer']['image']['thumbnails'][-1]['url'])
        return links
    except KeyError as error:
        logging.debug(f'[POST_ID: {post["postId"]}] function: _handle_multi_images: KeyError {error}')
        return None


def _handle_single_image(post: dict) -> str | None:
    try:
        return post['backstageAttachment']['backstageImageRenderer']['image']['thumbnails'][-1]['url']
    except KeyError as error:
        logging.debug(f'[POST_ID: {post["postId"]}] function: _handle_single_image: KeyError {error}')
        return None


def _get_image_links(post: dict) -> list[str] | None:
    image_link = _handle_single_image(post)
    if image_link:
        return [image_link]

    image_links = _handle_multi_images(post)
    if image_links:
        return image_links

    return None


def _handle_text(content: dict) -> str:
    try:
        link_redirect = content['navigationEndpoint']['urlEndpoint']['url']
        # for some reason the url can be messy in strange ways (idk why; maybe it's just user error); a smart sanitizer would be great
        link = re.findall(pattern="(?<=q=)(.+)", string=link_redirect)
        url = urllib.parse.unquote(link[0])
        # print(f"Link: {link} \nURL: {url}")
        return url
    except IndexError as error:
        # youtube link, so not redirect
        return link_redirect
    except KeyError as error:
        return content['text']


def _get_text(post: dict) -> str | None:
    try:
        text = post['contentText']['runs']
        strings = [_handle_text(content) for content in text]
        return ''.join(strings)
    except KeyError:  # sharedPostRenderer
        try:
            text = post['content']['runs']
            strings = [_handle_text(content) for content in text]
            return ''.join(strings)
        except KeyError as error:
            logging.debug(f'[POST_ID: {post["postId"]}] function: _get_text: KeyError {error}')
            return None


def _get_content(post: dict) -> dict:
    post_info = {
        "post_link": 'https://www.youtube.com/post/' + post['postId'],
        "time_since": post['publishedTimeText']['runs'][0]['text'],
        "time_of_download": datetime.now(timezone.utc).strftime("%d/%m/%Y, %H:%M:%S"),
        "video": _get_video_link(post),
        "images": _get_image_links(post),
        "text": _get_text(post)
    }
    return post_info


class YoutubePosts:
    def __init__(self, channel_link: str, cookies: dict) -> None:
        self.posts = list()
        self.api_key = None
        self.api_url = None
        self.token = None
        self.channel_link = channel_link
        self.channel_name = channel_link[24:] if channel_link[-1] != '/' else channel_link[24:-1]
        self.link = channel_link + '/posts'
        self.session = AsyncHTMLSession()
        self.cookies = cookies
        self.taskID = None

    async def request(self, init: bool = True) -> HTMLResponse:
        if init:
            return await self.session.get(url=self.link + '?persist_hl=1&hl=en', cookies=self.cookies)
        else:
            payload = _payload(token=self.token, originalURL=self.link)
            return await self.session.post('https://www.youtube.com' + self.api_url + '?key=' + self.api_key + '&prettyPrint=false', json=payload)

    def __get_API_key(self, response: HTMLResponse) -> None:
        try:
            string = response.html.find("script", containing='\"INNERTUBE_API_KEY\":')[0].text
            self.api_key = re.search(pattern="(?<=\"INNERTUBE_API_KEY\":\")(.+?)(?=\")", string=string)[0]
        except IndexError:
            logging.warning(f'{self.channel_name}: API key not found.')

    def __get_API_URL(self, response: HTMLResponse) -> None:
        try:
            string = response.html.find("script", containing='\"apiUrl\":')[0].text
            self.api_url = re.search(pattern="(?<=\"apiUrl\":\")(.+?)(?=\")", string=string)[0]
        except IndexError:
            logging.warning(f'{self.channel_name}: API URL not found.')

    def __get_token(self, response: HTMLResponse) -> None:
        try:
            string = response.html.find("script", containing='\"token\":')[0].text
            self.token = re.search(pattern="(?<=\"token\":\")(.+?)(?=\")", string=string)[0]
        except IndexError:
            logging.warning(f'{self.channel_name}: Continuation token not found.')

    def __get_init_posts(self, response: HTMLResponse) -> bool:

        try:
            string = response.html.find("script", containing='\"backstagePostThreadRenderer\":')[0].text
            posts = re.findall(pattern="({\"backstagePostThreadRenderer\":)(.+?)(\"}}}}(?=(,{)|(],)))", string=string)

            json_posts = [json.loads(post[1] + post[2][:-1]) for post in posts]
            for post in json_posts:
                try:
                    postRenderer = post['post']['backstagePostRenderer']
                except KeyError:
                    postRenderer = post['post']['sharedPostRenderer']
                self.posts.append(_get_content(post=postRenderer))
        except IndexError as error:
            logging.debug("No init posts", exc_info=True)
            return False

        return True

    async def scrape(self, pbar: Progress | None, limit: int | None = None) -> None:
        response = await self.request(init=True)
        self.__get_API_key(response)
        self.__get_API_URL(response)
        self.__get_token(response)
        init_posts = self.__get_init_posts(response)

        total = len(self.posts)
        init_total = total

        # Only add a task to pbar if it exists (allows module use without UI)
        if pbar:
            self.taskID = pbar.add_task(f"{self.channel_name}", total=total, new=None)
        else:
            self.taskID = None

        eof = False
        while not eof and (not init_total < 10 or not init_posts):
            # Check if the post limit has been reached at the start of the loop
            if limit and len(self.posts) >= limit:
                logging.debug(f"Scrape limit reached: {limit}")
                break

            response = await self.request(init=False)
            response = json.loads(response.text)
            try:
                posts = response['onResponseReceivedEndpoints'][0]['appendContinuationItemsAction']['continuationItems']
                if total == 0:
                    try:
                        posts[0]['aboutChannelRenderer']  # if this exists then channel has no posts
                        logging.debug("No posts found")
                        break
                    except Exception:
                        pass
            except KeyError:
                if not init_posts:
                    logging.warning("No posts found. Maybe YouTube changed its API response format?")
                break

            total += len(posts) - 1
            if pbar:
                pbar.update(self.taskID, total=total)

            for i, post in enumerate(posts):
                # Check limit again before processing individual posts from the batch
                if limit and len(self.posts) >= limit:
                    eof = True  # Set eof flag to also stop the outer while loop
                    logging.debug(f"Scrape limit reached during loop: {limit}")
                    break

                if i != len(posts) - 1:
                    try:
                        postRenderer = post['backstagePostThreadRenderer']['post']['backstagePostRenderer']
                    except KeyError as error:
                        logging.debug(f"Post({total - len(posts) - 1 + i}): sharedPostRenderer")
                        postRenderer = post['backstagePostThreadRenderer']['post']['sharedPostRenderer']
                    self.posts.append(_get_content(post=postRenderer))
                    if pbar:
                        pbar.update(self.taskID, advance=1)
                else:
                    try:
                        self.token = post['continuationItemRenderer']['continuationEndpoint']['continuationCommand']['token']
                    except KeyError:
                        logging.debug("No token at end of posts.")
                        eof = True
                        try:
                            postRenderer = post['backstagePostThreadRenderer']['post']['backstagePostRenderer']
                            self.posts.append(_get_content(post=postRenderer))
                            total += 1
                            if pbar:
                                pbar.update(self.taskID, advance=1, total=total)
                        except KeyError:
                            logging.warning("Initial suspected token was not a post either.")

        # this is here for the spinner to keep spinning without interruptions
        if pbar:
            pbar.update(self.taskID, advance=init_total)
        self.posts.reverse()  # reverses post order from newest first to oldest first

    def save(self, pbar: Progress | None, folder: str | None = None, reverse: bool = False, update: bool = False) -> None:
        if folder:
            folder += '/' if folder[-1] != '/' else ''
            os.makedirs(folder, exist_ok=True)  # create folder if it doesn't exist
        if reverse:
            self.posts.reverse()
        if update:
            try:
                with open(f'{folder}{self.channel_name}_posts.json', 'r', encoding='utf-8') as target:
                    oldposts = json.load(target)
                    oldpostIDs = {post['post_link']: 'post_link' for post in oldposts}
                    i = 0
                    for post in self.posts[:]:
                        try:
                            oldpostIDs[post['post_link']]
                            self.posts.pop(i)
                        except KeyError:
                            i += 1
                    if pbar:
                        pbar.update(self.taskID, new=len(self.posts))
                    self.posts = list(itertools.chain(oldposts, self.posts))
            except FileNotFoundError:
                # Handle case where --update is used but the JSON file doesn't exist yet
                logging.debug(f"Update file not found, creating new file: {self.channel_name}_posts.json")
                if pbar:
                    pbar.update(self.taskID, new=len(self.posts))

        with open(f'{folder}{self.channel_name}_posts.json', 'w', encoding='utf-8') as target:
            json.dump(self.posts, target, ensure_ascii=False, indent=4)


def get_arg_parser():
    ap = argparse.ArgumentParser(prog='yp-dl',
                                 description='An asynchronous scraper that downloads youtube posts from youtube channels in json format.')
    ap.add_argument('link', nargs='+',
                    help='Provide any number of links. Link example: https://www.youtube.com/@3blue1brown')
    ap.add_argument('-f', '--folder-path', dest='folderpath', metavar='FOLDER_PATH', type=str, default='',
                    help='Provide the path of the folder you wish to store/update your json files. If it\'s in the current working directory (CWD), just type the folder name. If none is provided, everything will be stored/updated in the CWD.')
    ap.add_argument('-r', '--reverse', action='store_true',
                    help='Reverses the order of the posts from oldest first to newest first. Be wary though, if you use this option with --update, your post order will be messed up.')
    ap.add_argument('-u', '--update', action='store_true',
                    help='Appends the existing json file(s) with the new posts.')
    ap.add_argument('-v', '--verbose', action='store_true',
                    help='Gives more details about what\'s going on when the program runs.')
    ap.add_argument('-o', '--overwrite-cookie', action='store_true',
                    help='Overwrites the SOCS cookie in the cookies.txt file with a Default SOCS cookie within the project. Use if having problems retrieving posts.')
    ap.add_argument('-d', '--delete-cookie', action='store_true',
                    help='Removes the cookie file to generate it again. Use if your SOCS key has expired (lifetime is 2 years).')
    ap.add_argument('-l', '--limit', dest='limit', metavar='N', type=int, default=None,
                    help='Stops scraping after collecting N posts (from newest). Useful for checking recent posts.')

    return ap


def get_pbar(update: bool = False):
    if update:
        pbar = Progress(TextColumn("[progress.description]{task.description}"),
                        SpinnerColumn(),
                        TextColumn("[#9B59B6]{task.completed}/{task.total}"),
                        TimeElapsedColumn(),
                        TextColumn("[cyan]New posts: {task.fields[new]}"))
    else:
        pbar = Progress(TextColumn("[progress.description]{task.description}"),
                        SpinnerColumn(),
                        TextColumn("[#9B59B6]{task.completed}/{task.total}"),
                        TimeElapsedColumn())

    return pbar

# TODO: maybe add polls? need specific cookies to unlock poll votes


def run():
    ap = get_arg_parser()
    args = vars(ap.parse_args())

    if args['verbose']:
        logging.getLogger().setLevel(logging.DEBUG)

    if args['overwrite_cookie']:
        _handle_cookie_file('w', cookie=DEFAULT_SOCS_COOKIE)

    if args['delete_cookie']:
        try:
            os.remove(COOKIE_PATH)
        except FileNotFoundError:
            logging.warning("cookies.txt not found")

    cookies = {"SOCS": get_SOCS_cookie()}
    objects = [YoutubePosts(link, cookies) for link in args['link']]
    pbar = get_pbar(args['update'])

    tasks = [obj.scrape(pbar, limit=args['limit']) for obj in objects]
    loop = asyncio.get_event_loop()
    with pbar:
        loop.run_until_complete(asyncio.gather(*tasks))
        for obj in objects:
            obj.save(pbar, folder=args['folderpath'], reverse=args['reverse'], update=args['update'])


if __name__ == "__main__":
    run()
