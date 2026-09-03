JHU Software Concepts - Module 1
Personal Portfolio Site (Flask)


DESCRIPTION

This project is a personal portfolio website built with Flask, created as
an assignment for the Johns Hopkins Modern Software Concepts course
(EN.605.256.82). The site includes a homepage with a short bio, a contact
page, a projects page, and a publications page, all sharing a common
navigation bar.


PREREQUISITES

- Python 3.10 or higher


SETUP INSTRUCTIONS

You can run this project using either a Python virtual environment or a
conda environment.

Option A - Python virtual environment:

1. From the module_1 directory, create a virtual environment:

     python3 -m venv venv

2. Activate the virtual environment:

     On macOS/Linux:   source venv/bin/activate
     On Windows:        venv\Scripts\activate

3. Install the required dependencies:

     pip install -r requirements.txt

Option B - Conda environment:

1. Create and activate a conda environment (any recent Python 3 version):

     conda create -n jhu_software python=3.12
     conda activate jhu_software

2. Install the required dependencies:

     pip install -r requirements.txt


RUNNING THE APP

With your environment active and your terminal in the module_1 directory,
start the application by running:

     python run.py

The Flask development server will start and print startup messages to the
terminal. Leave this terminal window open while you use the site; press
Ctrl+C to stop the server when you are done.


VIEWING THE SITE

Once the server is running, open a web browser and go to:

     http://localhost:8080

or equivalently:

     http://0.0.0.0:8080


PROJECT STRUCTURE

module_1/
  run.py                  Entry point that creates and runs the Flask app.
  requirements.txt        Python package dependencies.
  README.txt              This file.
  app/
    __init__.py            Application factory (create_app) that builds
                            the Flask app and registers blueprints.
    main/
      __init__.py           Marks this directory as a Python package.
      routes.py             Defines the "main" blueprint and its routes
                            (Home, Contact, Projects, Publications).
    templates/              Jinja2 HTML templates.
      base.html              Shared page layout and navigation bar, used
                            by every page via template inheritance.
      index.html              Homepage content.
      contact.html            Contact page content.
      projects.html           Projects page content.
      publications.html       Publications page content.
    static/                 Static assets served directly by Flask.
      css/                    Stylesheets (nav.css, pages.css).
      images/                 Image assets (e.g. headshot.jpg).

The application uses Flask's application factory pattern together with a
Blueprint (main_bp) to keep route definitions organized. All page templates
extend base.html, which contains the shared navigation bar and highlights
the current page using Flask's request object.
