import uuid
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure, PyMongoError
import resend
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from datetime import datetime, timezone
import os
import logging
import asyncio
from typing import Union
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI(
    title="Portfolio Project Request API",
    description="API for submitting project, hiring, and contact requests to Muhammad Ahmad's portfolio.",
    version="1.1.0"
)

# CORS configuration
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8080,https://m-ahmad-portfolio-dev.netlify.app").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept", "X-Requested-With"],
)

# MongoDB configuration
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    logger.error("MONGO_URI is not set in environment variables")
    raise ValueError("MONGO_URI is not set in environment variables")

# Resend configuration
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
if not RESEND_API_KEY:
    logger.warning("RESEND_API_KEY not set. Email notifications will not work.")
else:
    resend.api_key = RESEND_API_KEY

# Initialize MongoDB client
client = None
db = None
collection = None

# Pydantic models for request validation
class ProjectDetails(BaseModel):
    clientType: str | None = None
    clientName: str | None = None
    companyName: str | None = None
    projectType: str | None = None
    budget: str | None = None
    timeline: str | None = None
    requirements: str | None = None
    contactEmail: EmailStr

class HiringDetails(BaseModel):
    clientType: str = "company"
    companyName: str
    positionTitle: str
    budget: str
    timeline: str
    requirements: str
    contactEmail: EmailStr

class ContactDetails(BaseModel):
    name: str
    email: EmailStr
    message: str

# Helper function to validate project details
def validate_project_details(details: ProjectDetails) -> None:
    if not details.contactEmail:
        raise HTTPException(status_code=400, detail="Contact email is required")
    if details.clientType and details.clientType not in ["company", "individual"]:
        raise HTTPException(status_code=400, detail="Invalid client type")
    if details.clientType == "company" and not details.companyName:
        raise HTTPException(status_code=400, detail="Company name is required for company client type")
    if details.clientType == "individual" and not details.clientName:
        raise HTTPException(status_code=400, detail="Client name is required for individual client type")

# Helper function to validate hiring details
def validate_hiring_details(details: HiringDetails) -> None:
    if details.clientType != "company":
        raise HTTPException(status_code=400, detail="Client type must be 'company' for hiring requests")
    if not details.companyName.strip():
        raise HTTPException(status_code=400, detail="Company name is required")
    if not details.positionTitle.strip():
        raise HTTPException(status_code=400, detail="Position title is required")
    if not details.budget.strip():
        raise HTTPException(status_code=400, detail="Budget is required")
    if not details.timeline.strip():
        raise HTTPException(status_code=400, detail="Timeline is required")
    if not details.requirements.strip():
        raise HTTPException(status_code=400, detail="Requirements are required")
    if not details.contactEmail:
        raise HTTPException(status_code=400, detail="Contact email is required")

# Helper function to validate contact details
def validate_contact_details(details: ContactDetails) -> None:
    if not details.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if not details.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")
    if len(details.message) > 1000:
        raise HTTPException(status_code=400, detail="Message cannot exceed 1000 characters")

# Retry decorator for MongoDB connection
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type(ConnectionFailure),
    before_sleep=lambda retry_state: logger.info(f"Retrying MongoDB connection (attempt {retry_state.attempt_number})...")
)
async def connect_to_mongodb():
    global client, db, collection
    client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    await client.admin.command("ping")
    db = client["portfolio"]
    collection = db["project_requests"]
    logger.info("MongoDB connection established")

# Consolidated helper function to send email via Resend with retry
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=5),
    retry=retry_if_exception_type(Exception),
    before_sleep=lambda retry_state: logger.info(f"Retrying email send (attempt {retry_state.attempt_number})...")
)
async def send_email(details: Union[ProjectDetails, HiringDetails, ContactDetails]) -> bool:
    if not RESEND_API_KEY:
        logger.warning("Resend API key not set. Skipping email send.")
        return False

    try:
        email_content = {
            "from": "onboarding@resend.dev",
            "to": "ahmadrajpootr1@gmail.com",
            "subject": "",
            "html": ""
        }

        if isinstance(details, ProjectDetails):
            email_content["subject"] = "New Project Request from AI Assistant"
            email_content["html"] = (
                f"<h3>New Project Request</h3>"
                f"<p><strong>Client Type:</strong> {details.clientType or 'Not specified'}</p>"
                f"<p><strong>{'Company Name' if details.clientType == 'company' else 'Client Name'}:</strong> "
                f"{details.companyName or details.clientName or 'Not specified'}</p>"
                f"<p><strong>Project Type:</strong> {details.projectType or 'Not specified'}</p>"
                f"<p><strong>Budget:</strong> {details.budget or 'Not specified'}</p>"
                f"<p><strong>Timeline:</strong> {details.timeline or 'Not specified'}</p>"
                f"<p><strong>Requirements:</strong> {details.requirements or 'Not specified'}</p>"
                f"<p><strong>Contact Email:</strong> {details.contactEmail}</p>"
                f"<p><strong>Received At:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</p>"
            )
        elif isinstance(details, HiringDetails):
            email_content["subject"] = "New Hiring Request from AI Assistant"
            email_content["html"] = (
                f"<h3>New Hiring Request</h3>"
                f"<p><strong>Client Type:</strong> {details.clientType}</p>"
                f"<p><strong>Company Name:</strong> {details.companyName}</p>"
                f"<p><strong>Position Title:</strong> {details.positionTitle}</p>"
                f"<p><strong>Budget:</strong> {details.budget}</p>"
                f"<p><strong>Timeline:</strong> {details.timeline}</p>"
                f"<p><strong>Requirements:</strong> {details.requirements}</p>"
                f"<p><strong>Contact Email:</strong> {details.contactEmail}</p>"
                f"<p><strong>Received At:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</p>"
            )
        else:  # ContactDetails
            email_content["subject"] = "New Contact Form Submission"
            email_content["html"] = (
                f"<h3>New Contact Form Submission</h3>"
                f"<p><strong>Name:</strong> {details.name}</p>"
                f"<p><strong>Email:</strong> {details.email}</p>"
                f"<p><strong>Message:</strong> {details.message}</p>"
                f"<p><strong>Received At:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</p>"
            )

        response = await asyncio.to_thread(resend.Emails.send, email_content)
        logger.info(f"Email sent successfully: Message ID {response['id']}")
        return True
    except Exception as e:
        logger.error(f"Resend error: {str(e)}")
        raise

# Explicit OPTIONS handler for CORS preflight requests
@app.options("/api/{path:path}")
async def handle_options():
    return JSONResponse(
        content={"status": "ok"},
        headers={
            "Access-Control-Allow-Origin": ",".join(ALLOWED_ORIGINS),
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization,Accept,X-Requested-With",
            "Access-Control-Allow-Credentials": "true"
        }
    )

# FastAPI startup event
@app.on_event("startup")
async def startup_event():
    try:
        await connect_to_mongodb()
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB after retries: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")

# FastAPI shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    global client
    if client:
        client.close()
        logger.info("MongoDB connection closed")

@app.post(
    "/api/project-request",
    summary="Submit a project request",
    response_description="Confirmation message and request ID"
)
async def submit_project_request(details: ProjectDetails):
    try:
        validate_project_details(details)
        project_data = details.model_dump(exclude_unset=True)
        project_data["created_at"] = datetime.now(timezone.utc)
        project_data["type"] = "project_request"
        project_data["request_id"] = str(uuid.uuid4())

        result = await collection.insert_one(project_data)
        if not result.inserted_id:
            raise PyMongoError("Failed to store project details in MongoDB")

        email_sent = await send_email(details)
        return {
            "message": "Project request submitted successfully. Muhammad Ahmad will contact you soon!",
            "request_id": project_data["request_id"],
            "email_sent": email_sent
        }
    except HTTPException as e:
        raise e
    except PyMongoError as e:
        logger.error(f"MongoDB error: {e}")
        raise HTTPException(status_code=500, detail="Failed to store project details")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred. Please try again or contact Muhammad directly at ahmadrajpootr1@gmail.com."
        )

@app.post(
    "/api/hiring-request",
    summary="Submit a hiring request",
    response_description="Confirmation message and request ID"
)
async def submit_hiring_request(details: HiringDetails):
    try:
        validate_hiring_details(details)
        hiring_data = details.model_dump()
        hiring_data["created_at"] = datetime.now(timezone.utc)
        hiring_data["type"] = "hiring_request"
        hiring_data["request_id"] = str(uuid.uuid4())

        result = await collection.insert_one(hiring_data)
        if not result.inserted_id:
            raise PyMongoError("Failed to store hiring details in MongoDB")

        email_sent = await send_email(details)
        return {
            "message": "Hiring request submitted successfully. Muhammad Ahmad will contact you soon!",
            "request_id": hiring_data["request_id"],
            "email_sent": email_sent
        }
    except HTTPException as e:
        raise e
    except PyMongoError as e:
        logger.error(f"MongoDB error: {e}")
        raise HTTPException(status_code=500, detail="Failed to store hiring details")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred. Please try again or contact Muhammad directly at ahmadrajpootr1@gmail.com."
        )

@app.post(
    "/api/contact",
    summary="Submit a contact form",
    response_description="Confirmation message and request ID"
)
async def submit_contact_request(details: ContactDetails):
    try:
        validate_contact_details(details)
        contact_data = details.model_dump()
        contact_data["created_at"] = datetime.now(timezone.utc)
        contact_data["type"] = "contact_request"
        contact_data["request_id"] = str(uuid.uuid4())

        result = await collection.insert_one(contact_data)
        if not result.inserted_id:
            raise PyMongoError("Failed to store contact details in MongoDB")

        email_sent = await send_email(details)
        return {
            "message": "Your message has been sent to Muhammad Ahmad. He will contact you soon!",
            "request_id": contact_data["request_id"],
            "email_sent": email_sent
        }
    except HTTPException as e:
        raise e
    except PyMongoError as e:
        logger.error(f"MongoDB error: {e}")
        raise HTTPException(status_code=500, detail="Failed to store contact details")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(
            status_code=500,
            detail="An error occurred. Please try again or contact Muhammad directly at ahmadrajpootr1@gmail.com."
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)