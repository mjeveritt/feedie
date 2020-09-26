from setuptools import setup  # type: ignore

setup(name='feedie',
      version='1.1',
      description='The funniest joke in the world',
      url='https://github.com/vlstill/feedie',
      author='meigrafd, Vladimír Štill',
      author_email='meigrafd <meiraspi@gmail.com>, '
                   'Vladimír Štill <feedie@vstill.eu>',
      license='Creative Commons License (BY-NC-SA)',
      packages=['feedie'],
      install_requires=[
          'pyopenssl',
          'feedparser',
          'irc',
          'requests',
          'sgmllib3k'
      ],
      entry_points={
          'console_scripts': ['feedie=feedie:main'],
      },
      zip_safe=False)
