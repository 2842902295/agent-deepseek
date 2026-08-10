declare namespace Api {
  /**
   * namespace Auth
   *
   * backend api module: "auth"
   */
  namespace Auth {
    interface LoginToken {
      token: string;
      refreshToken: string;
    }

    interface UserInfo {
      userId: string;
      userName: string;
      nickName: string;
      roles: string[];
      buttons: string[];
      /** 邮箱（GET /auth/user-info 返回；类型补齐供个人资料编辑用） */
      userEmail?: string | null;
      /** 手机号（GET /auth/user-info 返回；类型补齐供个人资料编辑用） */
      userPhone?: string | null;
      /** 性别：'1' 男 / '2' 女 / '3' 保密（GET /auth/user-info 返回） */
      userGender?: string;
    }
  }
}
