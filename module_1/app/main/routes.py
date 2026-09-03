from flask import Blueprint, render_template

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    return render_template("index.html")


@main_bp.route("/contact")
def contact():
    return render_template("contact.html")


@main_bp.route("/projects")
def projects():
    return render_template("projects.html")


@main_bp.route("/publications")
def publications():
    return render_template("publications.html")
