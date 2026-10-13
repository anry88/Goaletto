"""Cosmetic international-v1 names. No portrait, gameplay or locale inputs."""

from hashlib import sha256
from itertools import product
from random import Random

NAMING_POLICY = "international-v1"
# Distinct language traditions; spelling pools are paired only within a row.
# International display order is given name then family name. Vietnamese names
# retain family-middle-given order through the corresponding tuple below.
NAME_POOLS = (
    (
        "slavic",
        "Aleksandr Alexei Andrei Artem Boris Ivan Pavel Roman",
        "Belov Volkov Gromov Morozov Petrov Sokolov Orlov Kuznetsov",
    ),
    (
        "polish",
        "Adam Jakub Jan Piotr Kamil Tomasz Michał Łukasz",
        "Nowak Kowalski Wiśniewski Wójcik Kamiński Lewandowski Zieliński Szymański",
    ),
    (
        "serbian",
        "Aleksandar Nikola Marko Stefan Luka Miloš Dušan Filip",
        "Petrović Jovanović Nikolić Đorđević Stojanović Ilić Pavlović Kovačević",
    ),
    (
        "spanish",
        "Alejandro Álvaro Diego Javier José Manuel Miguel Sergio",
        "García Fernández López Martínez Rodríguez Sánchez Torres Díaz",
    ),
    (
        "portuguese",
        "João Pedro Luís André Diogo Gonçalo Tiago Tomás",
        "Silva Santos Ferreira Pereira Costa Oliveira Rodrigues Almeida",
    ),
    (
        "french",
        "Antoine Benoît Étienne François Julien Louis Matthieu Raphaël",
        "Bernard Dubois Moreau Laurent Lefèvre Michel Fournier Girard",
    ),
    (
        "italian",
        "Alessandro Andrea Davide Francesco Lorenzo Matteo Marco Paolo",
        "Rossi Russo Ferrari Esposito Bianchi Romano Ricci Marino",
    ),
    (
        "german",
        "Felix Jonas Lukas Leon Julian Maximilian Paul Tobias",
        "Müller Schmidt Schneider Fischer Weber Meyer Wagner Becker",
    ),
    (
        "english",
        "James Oliver William George Harry Jack Thomas Edward",
        "Smith Jones Taylor Brown Wilson Johnson Davies Robinson",
    ),
    (
        "turkish",
        "Ahmet Mehmet Mustafa Emre Deniz Burak Hakan Can",
        "Yılmaz Kaya Demir Şahin Çelik Yıldız Aydın Arslan",
    ),
    (
        "arabic_maghreb",
        "Amine Youssef Mehdi Anas Hamza Walid Sofiane Karim",
        "Bennani El-Idrissi Alaoui Berrada Tazi Benali Saidi Mansouri",
    ),
    (
        "yoruba",
        "Adewale Ayodele Babatunde Oluwaseun Olumide Olawale Adebayo Kayode",
        "Adeyemi Akinyemi Akinola Adebisi Adeoye Ogunleye Adesina Ajayi",
    ),
    (
        "igbo",
        "Chinedu Emeka Chidi Obinna Nnamdi Chukwudi Ikechukwu Kelechi",
        "Okafor Okeke Nwosu Eze Uche Nwachukwu Onwughalu Ojukwu",
    ),
    (
        "hausa",
        "Musa Sani Umar Ibrahim Abubakar Abdullahi Garba Aminu",
        "Bello Danjuma Shehu Sule Audu Dandago Adamu Usman",
    ),
    (
        "wolof",
        "Mamadou Ibrahima Cheikh Ousmane Babacar Moustapha Abdoulaye Pape",
        "Diop Ndiaye Fall Seck Dieng Sarr Faye Guèye",
    ),
    (
        "akan",
        "Kwame Kofi Kwesi Kojo Kwaku Yaw Kwabena Akwasi",
        "Mensah Owusu Osei Asante Boateng Agyeman Antwi Appiah",
    ),
    (
        "mandinka",
        "Lamin Ebrima Bakary Alieu Ousman Momodou Amadou Malang",
        "Jallow Ceesay Sanyang Touray Jammeh Camara Darboe Sanneh",
    ),
    (
        "kikuyu",
        "Kamau Njoroge Njuguna Maina Mwangi Kariuki Mugo Githinji",
        "Waweru Wambugu Karanja Gachanja Kimani Muchiri Muriithi Ndungu",
    ),
    (
        "zulu",
        "Sipho Thabo Sibusiso Bongani Themba Sandile Sizwe Mandla",
        "Dlamini Zulu Khumalo Mkhize Ndlovu Buthelezi Ngcobo Mthembu",
    ),
    (
        "japanese",
        "Haruto Yuto Ren Sota Daiki Takumi Kaito Ryota",
        "Sato Suzuki Takahashi Tanaka Watanabe Ito Yamamoto Nakamura",
    ),
    (
        "korean",
        "Min-jun Seo-jun Ji-ho Do-yun Ye-jun Jun-seo Si-woo Ha-jun",
        "Kim Lee Park Choi Jung Kang Cho Yoon",
    ),
    ("chinese", "Wei Jun Ming Jie Lei Tao Hao Peng", "Wang Li Zhang Liu Chen Yang Zhao Huang"),
    (
        "hindi",
        "Aarav Arjun Aditya Akash Rohan Rahul Vikram Amit",
        "Sharma Verma Gupta Joshi Mishra Tiwari Kapoor Malhotra",
    ),
    (
        "bengali",
        "Arup Anirban Debashish Dipankar Subrata Suman Pranab Soumitra",
        "Das Dutta Sen Ghosh Bose Saha Roy Sarkar",
    ),
    (
        "thai",
        "Somchai Somsak Anan Niran Kittisak Chaiwat Prasert Apichat",
        "Saetang Saelim Saelee Saechen Wongchai Chaiyaporn Srisuk Boonmee",
    ),
    (
        "indonesian",
        "Agus Budi Dedi Eko Fajar Hendra Joko Wahyu",
        "Santoso Prasetyo Saputra Setiawan Kurniawan Wijaya Susanto Wibowo",
    ),
)
VIETNAMESE_FAMILIES = ("Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Phan", "Vũ", "Đặng")
VIETNAMESE_GIVEN = (
    "Văn An",
    "Văn Bình",
    "Minh Đức",
    "Quốc Huy",
    "Văn Nam",
    "Minh Quân",
    "Đức Thành",
    "Văn Tuấn",
)
INTERNATIONAL_CLUBS = (
    "Iskra",
    "Vostok",
    "Meridian",
    "Sputnik",
    "Aurora",
    "Atlas",
    "Riviera",
    "Montaña",
    "Estrela",
    "Mar Azul",
    "Alvorada",
    "Horizonte",
    "Lumière",
    "Bellevue",
    "Étoile",
    "Valdora",
    "Soleil",
    "Porto Verde",
    "Granito",
    "Levante",
    "Oakbridge",
    "Ravenshill",
    "Northgate",
    "Silverbrook",
    "Westhaven",
    "Sonnenfels",
    "Adlerhöhe",
    "Birkenau",
    "Riverstone",
    "Fairfield",
    "Baobab",
    "Teranga",
    "Savanna",
    "Ubuntu",
    "Nyota",
    "Kijani",
    "Amani",
    "Jua",
    "Dakar Waves",
    "Accra United",
    "Hikari",
    "Sakura",
    "Aozora",
    "Han River",
    "Haedong",
    "Lotus",
    "Mekong",
    "Banyan",
    "Garuda",
    "Nusantara",
)


def international_catalog(seed: int) -> tuple[tuple[str, str], ...]:
    """Balanced shuffled round-robin: each of 27 pools supplies 37–38 names."""
    rng = Random(int.from_bytes(sha256(f"{NAMING_POLICY}:{seed}".encode()).digest(), "big"))
    pools = []
    for tradition, first, last in NAME_POOLS:
        names = [f"{a} {b}" for a, b in product(first.split(), last.split())]
        rng.shuffle(names)
        pools.append((tradition, names))
    vietnamese = [f"{a} {b}" for a, b in product(VIETNAMESE_FAMILIES, VIETNAMESE_GIVEN)]
    rng.shuffle(vietnamese)
    pools.append(("vietnamese", vietnamese))
    rng.shuffle(pools)
    result = tuple(
        (pools[i % len(pools)][0], pools[i % len(pools)][1][i // len(pools)]) for i in range(1000)
    )
    assert len({name for _, name in result}) == 1000
    return result


_CYRILLIC = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        (
            "a",
            "b",
            "v",
            "g",
            "d",
            "e",
            "yo",
            "zh",
            "z",
            "i",
            "y",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "r",
            "s",
            "t",
            "u",
            "f",
            "kh",
            "ts",
            "ch",
            "sh",
            "shch",
            "",
            "y",
            "",
            "e",
            "yu",
            "ya",
        ),
        strict=True,
    )
)


def latin_name(value: str) -> str:
    """Stable Russian legacy alias, without rewriting the source identity."""
    return "".join(
        _CYRILLIC.get(c.lower(), c).capitalize()
        if c.isupper() and c.lower() in _CYRILLIC
        else _CYRILLIC.get(c, c)
        for c in value
    )
