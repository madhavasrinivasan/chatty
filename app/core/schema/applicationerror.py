class ApplicationError(Exception):
    def __init__(self,
    *,
    type:str = "Something Went Wrong",
    message:str = "Something Went Wrong",
    discription:str | None = None,
    status_code:int = 400,
    errors:list | None = None,
    ):

        self.type = type
        self.message = message
        self.discription = discription
        self.status_code = status_code
        self.errors = errors
        super().__init__(message)

    
    @classmethod
    def SomethingWentWrong(cls, resourse: str):
        return cls(
             type="Something Went Wrong",
            message=resourse if resourse else "Something Went Wrong",
            status_code=400
        ) 
    
    @classmethod
    def Unauthorized(cls, resourse: str):
        return cls(
            type="Unauthorized",
            message=resourse if resourse else "Unauthorized",
            status_code=401
        )
    
    @classmethod
    def Forbidden(cls, resourse: str):
        return cls(
            type="Forbidden",
            message=resourse if resourse else "Forbidden",
            status_code=403
        )
    
    @classmethod
    def NotFound(cls, resourse: str):
        return cls(
            type="NotFound",
            message=resourse if resourse else "NotFound",
            status_code=404
        ) 

    @classmethod
    def BadRequest(cls, resourse: str):
        return cls(
            type="BadRequest",
            message=resourse if resourse else "BadRequest",
            status_code=400
        )
    
    @classmethod
    def InternalServerError(cls, resourse: str):
        return cls(
            type="InternalServerError", 
            message=resourse if resourse else "InternalServerError",
            status_code=500
        )
    
    @classmethod
    def ServiceUnavailable(cls, resourse: str):
        return cls(
            type="ServiceUnavailable",
            message=resourse if resourse else "ServiceUnavailable",
            status_code=503
        )