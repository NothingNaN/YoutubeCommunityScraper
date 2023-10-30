import itertools
from requests import Response
from requests_html import HTMLSession, AsyncHTMLSession
from lxml import etree
import re
import json
import logging
import sys
import asyncio
import argparse
from rich.progress import Progress, TextColumn, TimeElapsedColumn, SpinnerColumn
from datetime import datetime, timezone

logging.basicConfig(format='[%(levelname)s]: %(message)s', level=logging.WARNING, stream=sys.stdout)


def get_SOCS_cookie() -> str:
    session = HTMLSession()
    result = session.get(url='https://www.youtube.com/feed')

    form = result.html.xpath('/html/body/c-wiz/div/div/div/div[2]/div[1]/div[3]/div[1]/form[1]')[0].html
    root = etree.HTML(form)

    elements = root.xpath('//input')
    data = {element.attrib['name']: element.attrib['value'] for element in elements}
    result = session.post(url="https://consent.youtube.com/save", data=data)

    return session.cookies['SOCS']


def _payload(token: str, originalURL: str) -> dict:
    json_payload = {
        "context": {
            "client": {
                "userAgent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36,gzip(gfe)",
                "clientName": "WEB",
                "clientVersion": "2.20231010.10.01",
                "originalUrl": originalURL,
                "platform": "DESKTOP",
                "browserName": "Chrome",
                "browserVersion": "117.0.0.0",
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
        logging.debug(f'function: _get_video_link: {error}')
        return None


def _get_image_link(post: dict) -> str | None:
    try:
        return post['backstageAttachment']['backstageImageRenderer']['image']['thumbnails'][-1]['url']
    except KeyError as error:
        logging.debug(f'function: _get_image_link: {error}')
        return None


def _get_text(post: dict) -> str | None:
    try:
        return post['contentText']['runs'][0]['text']
    except KeyError as error:
        logging.debug(f'function: _get_text: {error}')
        return None


def _get_content(post: dict) -> dict:
    post_info = {
        "post_link": 'https://www.youtube.com/post/' + post['postId'],
        "time_since": post['publishedTimeText']['runs'][0]['text'],
        "time_of_download": datetime.now(timezone.utc).strftime("%d/%m/%Y, %H:%M:%S"),
        "video": _get_video_link(post),
        "image": _get_image_link(post),
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
        self.channel_name = channel_link[24:]
        self.link = channel_link + '/community'
        self.session = AsyncHTMLSession()
        self.cookies = cookies
        self.taskID = None

    async def request(self, init: bool = True) -> Response:
        if init:
            return await self.session.get(url=self.link + '?persist_hl=1&hl=en', cookies=self.cookies)
        else:
            payload = _payload(token=self.token, originalURL=self.link)
            return await self.session.post('https://www.youtube.com' + self.api_url + '?key=' + self.api_key + '&prettyPrint=false', json=payload)

    def __get_API_key(self, response: Response) -> None:
        try:
            string = response.html.find("script", containing='\"INNERTUBE_API_KEY\":')[0].text
            self.api_key = re.search(pattern="(?<=\"INNERTUBE_API_KEY\":\")(.+?)(?=\")", string=string)[0]
        except IndexError:
            logging.warning(f'{self.channel_name}: API key not found.')

    def __get_API_URL(self, response: Response) -> None:
        try:
            string = response.html.find("script", containing='\"apiUrl\":')[0].text
            self.api_url = re.search(pattern="(?<=\"apiUrl\":\")(.+?)(?=\")", string=string)[0]
        except IndexError:
            logging.warning(f'{self.channel_name}: API URL not found.')

    def __get_token(self, response: Response) -> None:
        try:
            string = response.html.find("script", containing='\"token\":')[0].text
            self.token = re.search(pattern="(?<=\"token\":\")(.+?)(?=\")", string=string)[0]
        except IndexError:
            logging.warning(f'{self.channel_name}: Continuation token not found.')

    def __get_init_posts(self, response: Response) -> None:
        string = response.html.find("script", containing='\"backstagePostThreadRenderer\":')[0].text
        posts = re.findall(pattern="({\"backstagePostThreadRenderer\":)(.+?)(\"enableDisplayloggerExperiment\":true}}}(?=,{))", string=string)

        json_posts = [json.loads(post[1] + post[2][:-1]) for post in posts]
        for post in json_posts:
            postRenderer = post['post']['backstagePostRenderer']
            self.posts.append(_get_content(post=postRenderer))

    async def scrape(self, pbar: Progress) -> None:
        response = await self.request(init=True)
        self.__get_API_key(response)
        self.__get_API_URL(response)
        self.__get_token(response)
        self.__get_init_posts(response)

        total = len(self.posts)
        init_total = total
        self.taskID = pbar.add_task(f"{self.channel_name}", total=total, new=None)

        eof = False
        while not eof:
            response = await self.request(init=False)
            response = json.loads(response.text)
            try:
                posts = response['onResponseReceivedEndpoints'][0]['appendContinuationItemsAction']['continuationItems']
            except KeyError:
                break

            total += len(posts) - 1
            pbar.update(self.taskID, total=total)

            for i, post in enumerate(posts):
                if i != len(posts) - 1:
                    postRenderer = post['backstagePostThreadRenderer']['post']['backstagePostRenderer']
                    self.posts.append(_get_content(post=postRenderer))
                    pbar.update(self.taskID, advance=1)
                else:
                    try:
                        self.token = post['continuationItemRenderer']['continuationEndpoint']['continuationCommand']['token']
                    except KeyError:
                        eof = True
                        postRenderer = post['backstagePostThreadRenderer']['post']['backstagePostRenderer']
                        self.posts.append(_get_content(post=postRenderer))
                        total += 1
                        pbar.update(self.taskID, advance=1, total=total)

        # this is here for the spinner to keep spinning without interruptions
        pbar.update(self.taskID, advance=init_total)
        self.posts.reverse()  # reverses post order from newest first to oldest first

    def save(self, pbar: Progress, folder: str | None = None, reverse: bool = False, update: bool = False) -> None:

        if folder:
            folder += '/' if folder[-1] != '/' else ''
        if reverse:
            self.posts.reverse()
        if update:
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

                pbar.update(self.taskID, new=len(self.posts))
                self.posts = list(itertools.chain(oldposts, self.posts))

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
    cookies = {"SOCS": get_SOCS_cookie()}
    ap = get_arg_parser()
    args = vars(ap.parse_args())

    if args['verbose']:
        logging.getLogger().setLevel(logging.DEBUG)

    objects = [YoutubePosts(link, cookies) for link in args['link']]
    pbar = get_pbar(args['update'])

    tasks = [obj.scrape(pbar) for obj in objects]
    loop = asyncio.get_event_loop()
    with pbar:
        loop.run_until_complete(asyncio.gather(*tasks))
        for obj in objects:
            obj.save(pbar, folder=args['folderpath'], reverse=args['reverse'], update=args['update'])

if __name__ == "__main__":
    run()
