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

logging.basicConfig(format='[%(levelname)s]: %(message)s', level=logging.WARNING, stream=sys.stdout)


def _get_SOCS_cookie() -> str:
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
        logging.debug(error)
        return None


def _get_image_link(post: dict) -> str | None:
    try:
        return post['backstageAttachment']['backstageImageRenderer']['image']['thumbnails'][-1]['url']
    except KeyError as error:
        logging.debug(error)
        return None


def _get_text(post: dict) -> str | None:
    try:
        return post['contentText']['runs'][0]['text']
    except KeyError as error:
        logging.debug(error)
        return None


def _get_content(post: dict) -> dict:
    post_info = {
        "post_link": 'https://www.youtube.com/post/' + post['postId'],
        "time_since": post['publishedTimeText']['runs'][0]['text'],
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
        # print(pbar_position)
        self.__get_API_key(response)
        self.__get_API_URL(response)
        self.__get_token(response)
        self.__get_init_posts(response)

        total = len(self.posts)
        init_total = total
        taskID = pbar.add_task(f"{self.channel_name}", total=total)

        eof = False
        while not eof:
            response = await self.request(init=False)
            response = json.loads(response.text)
            try:
                posts = response['onResponseReceivedEndpoints'][0]['appendContinuationItemsAction']['continuationItems']
            except KeyError:
                break

            total += len(posts) - 1
            pbar.update(taskID, total=total)

            for i, post in enumerate(posts):
                if i != len(posts) - 1:
                    postRenderer = post['backstagePostThreadRenderer']['post']['backstagePostRenderer']
                    self.posts.append(_get_content(post=postRenderer))
                    pbar.update(taskID, advance=1)
                else:
                    try:
                        self.token = post['continuationItemRenderer']['continuationEndpoint']['continuationCommand']['token']
                    except KeyError:
                        eof = True
                        postRenderer = post['backstagePostThreadRenderer']['post']['backstagePostRenderer']
                        self.posts.append(_get_content(post=postRenderer))
                        total += 1
                        pbar.update(taskID, advance=1, total=total)

        # this is here for the spinner to keep spinning without interruptions
        pbar.update(taskID, advance=init_total)

    def save(self) -> None:
        with open(f'{self.channel_name}_posts.json', 'w', encoding='utf-8') as target:
            json.dump(self.posts, target, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    cookies = {"SOCS": _get_SOCS_cookie()}
    ap = argparse.ArgumentParser()
    ap.add_argument('links', nargs='+')
    args = vars(ap.parse_args())

    yt_links = args['links']
    objects = [YoutubePosts(link, cookies) for link in yt_links]

    pbar = Progress(TextColumn("[progress.description]{task.description}"),
                    SpinnerColumn(),
                    TextColumn("[#9B59B6]{task.completed}/{task.total}"), TimeElapsedColumn())
    tasks = [obj.scrape(pbar) for obj in objects]
    loop = asyncio.get_event_loop()
    with pbar:
        loop.run_until_complete(asyncio.gather(*tasks))

    for obj in objects:
        obj.save()
